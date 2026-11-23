"""GET/PUT /api/settings (read, validate and save config.yaml), GET /api/models (installed
local models with tested and fits-in-RAM flags), POST /api/reindex (whole library), and the
Gemini API: PUT/DELETE /api/gemini/key (saved in .env, never returned) and
POST /api/gemini/models (the models a key can use, free tier only unless config says otherwise)."""

from __future__ import annotations

import os
import re

import psutil
from fastapi import APIRouter, HTTPException, Request

from ..config import Config, ConfigError, config_path, get_config, load_config, parse_config
from ..config_writer import set_value
from ..env import GEMINI_KEY_VARS, EnvError, env_file_path, parse_env_text, set_env_value
from ..gemini_client import list_gemini_models
from ..llm import client_for
from ..ollama_client import OllamaError
from ..schemas import (ActionResult, GeminiInfo, GeminiKey, GeminiModel, GeminiModelList, GeminiModelsRequest, ModelInfo,
                       ModelList, SettingsResponse, SettingsSaved, SettingsUpdate)

router = APIRouter(prefix="/api")

# Spec section 8 (Settings): these changes show "Re-run the evaluation after saving".
RERUN_KEYS = {"provider.use_local_models", "gemini.answer_model", "gemini.embedding_model",
              "models.answer", "models.embedding", "reranker.enabled", "reranker.model", "retrieval.relevance_threshold",
              "retrieval.vector_threshold", "retrieval.final_k", "ingestion.chunk_size_tokens",
              "ingestion.chunk_overlap_tokens", "ollama.num_ctx"}
# Spec section 2 RAM budget: everything except the answer model (embeddings 0.3, reranker 1,
# Chroma/SQLite/FastAPI 1, browser 1, OS and other apps 4) is about 7.3 GB.
RESERVED_RAM_GB = 7.3


def _config_dict(cfg: Config) -> dict:
    """Settings as the page edits them. With Gemini on, models.* in memory holds the Gemini
    names, but the page shows (and saves) the local models from config.yaml there."""
    data = cfg.model_dump(mode="json", exclude={"base_dir"})
    if cfg.local_models is not None:
        data["models"] = cfg.local_models.model_dump(mode="json")
    return data


def _gemini_info(cfg: Config) -> GeminiInfo:
    source = None
    env_file = env_file_path()
    in_file = parse_env_text(env_file.read_text(encoding="utf-8")) if env_file.is_file() else {}
    for name in GEMINI_KEY_VARS:
        if (os.environ.get(name) or "").strip():
            source = ".env" if in_file.get(name) == os.environ[name] else name
            break
    return GeminiInfo(in_use=not cfg.provider.use_local_models, api_key_set=source is not None, api_key_source=source)


def _embedding(cfg: Config) -> tuple[str, str, str]:
    m = cfg.models
    return m.embedding, m.embedding_doc_prefix, m.embedding_query_prefix


def _reload(request: Request) -> Config:
    """Re-read config.yaml and .env, and rebuild the clients that use them."""
    s = request.app.state
    get_config.cache_clear()
    try:
        cfg = load_config(_path(request)).model_copy(update={"base_dir": s.config.base_dir})
    except ConfigError as exc:
        raise HTTPException(500, str(exc)) from exc
    s.build_services(request.app, cfg)
    return cfg


def _path(request: Request):
    return getattr(request.app.state, "config_file", None) or config_path()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request) -> SettingsResponse:
    p = _path(request)
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    cfg = request.app.state.config
    env = env_file_path()
    return SettingsResponse(config=_config_dict(cfg), yaml=text, path=str(p), env_overrides=list(cfg.env_overrides),
                            env_file=str(env) if env.is_file() else None, llm=cfg.llm.public(),
                            embed=cfg.embed.public(), gemini=_gemini_info(cfg),
                            active_answer_model=cfg.models.answer, active_embedding_model=cfg.models.embedding)


@router.put("/settings", response_model=SettingsSaved)
async def put_settings(body: SettingsUpdate, request: Request) -> SettingsSaved:
    s = request.app.state
    current = _config_dict(s.config)
    data = {k: dict(v) if isinstance(v, dict) else v for k, v in current.items()}
    changed = []
    for dotted, value in body.changes.items():
        section, _, key = dotted.partition(".")
        if section not in data or not isinstance(data[section], dict) or key not in data[section]:
            raise HTTPException(422, f"Unknown setting: {dotted}")
        if data[section][key] != value:
            data[section][key] = value
            changed.append(dotted)
    try:
        new_cfg = parse_config(data, base_dir=s.config.base_dir)  # validation refuses invalid values
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not changed:
        return SettingsSaved(saved=False, changed=[], rerun_evaluation=False, reindex_required=False,
                             message="Nothing changed.", config=current)
    p = _path(request)
    text = p.read_text(encoding="utf-8")
    for dotted in changed:
        section, _, key = dotted.partition(".")
        text = set_value(text, section, key, data[section][key])
    old = s.config
    p.write_text(text, encoding="utf-8")
    new_cfg = _reload(request)
    rerun = any(k in RERUN_KEYS for k in changed)
    # Re-index when the embedding model in use changes (also by switching between Gemini and
    # local models), or the chunk settings do.
    reindex = _embedding(old) != _embedding(new_cfg) or any(k.startswith("ingestion.chunk_") for k in changed)
    msg = "Saved to config.yaml."
    if rerun:
        msg += " Re-run the evaluation after saving."
    if reindex:
        msg += " Re-index the documents so they use the new settings."
    shadowed = [k for k in changed if k in s.config.env_overrides]
    if shadowed:
        msg += f" Note: .env still sets {', '.join(shadowed)}, and .env wins over config.yaml."
    return SettingsSaved(saved=True, changed=changed, rerun_evaluation=rerun, reindex_required=reindex, message=msg,
                         config=_config_dict(new_cfg))


@router.post("/reindex", response_model=ActionResult)
async def reindex_all(request: Request) -> ActionResult:
    """Rebuild every active document with the current embedding model and chunk settings."""
    s = request.app.state
    db, cfg = s.db, s.config
    model_changed = db.get_meta("embedding_model") not in (None, cfg.models.embedding)
    if model_changed:
        s.vectors.reset()  # a new model may have another vector size
    total = 0
    try:
        for v in db.query("SELECT id FROM versions WHERE status = 'active'"):
            total += (await s.ingestor.rebuild_version(v["id"]))["chunks"]
    except OllamaError as exc:
        raise HTTPException(502, f"Re-index stopped: {exc}") from exc
    with db.transaction() as conn:
        if model_changed:
            conn.execute("UPDATE versions SET embedding_model = NULL WHERE status != 'active'")
        db.set_meta("embedding_model", cfg.models.embedding, conn)
    return ActionResult(ok=True, message=f"Re-indexed every active document: {total} chunks.")


@router.get("/models", response_model=ModelList)
async def models(request: Request) -> ModelList:
    """Local models only (Ollama, or the server set in .env), whichever provider is on: the
    Settings page lists them when its "Use local models" toggle is on."""
    s = request.app.state
    cfg = s.config
    local = cfg.local_models or cfg.models
    try:
        if cfg.uses_gemini:
            async with client_for(cfg, cfg.local_llm, s.ollama_transport) as client:
                installed = await client.list_models()
        else:
            installed = await s.llm.list_models()
    except OllamaError as exc:
        return ModelList(models=[], default=local.answer, error=str(exc))
    budget = psutil.virtual_memory().total / 1024 ** 3 - RESERVED_RAM_GB
    out = []
    for m in installed:
        name = m.get("name") or m.get("model", "")
        details = m.get("details") or {}
        family = details.get("family") or ""
        families = details.get("families") or []
        is_emb = "bert" in family.lower() or any("bert" in f.lower() for f in families) or "embed" in name.lower()
        size_gb = round(m["size"] / 1024 ** 3, 1) if m.get("size") else None
        out.append(ModelInfo(name=name, size_gb=size_gb, family=family or None,
                             parameter_size=details.get("parameter_size"), is_embedding=is_emb,
                             tested=name == local.answer,
                             fits_in_ram=size_gb is None or size_gb * 1.2 <= budget))
    out.sort(key=lambda x: (not x.tested, x.is_embedding, x.name))
    return ModelList(models=out, default=local.answer)


# -- Gemini API --------------------------------------------------------------------------------

# Google's keys are letters, digits, - and _ ("AIza..."); this only stops obvious mistakes
# (spaces, quotes, a pasted line break) before the key is checked against the API.
_KEY_RE = re.compile(r"[A-Za-z0-9_\-.]{20,200}")


@router.post("/gemini/models", response_model=GeminiModelList)
async def gemini_models(body: GeminiModelsRequest, request: Request) -> GeminiModelList:
    """Every answer and embedding model this API key can use, each marked free tier or not.
    With gemini.free_tier_only (the default) the page greys out the paid-only ones, and
    saving one is refused."""
    s = request.app.state
    g = s.config.gemini
    key = (body.api_key or "").strip()
    if not key:
        saved = next((os.environ[n] for n in GEMINI_KEY_VARS if (os.environ.get(n) or "").strip()), "")
        key = saved.strip()
    try:
        found = await list_gemini_models(s.config, key, s.ollama_transport)
    except OllamaError as exc:
        return GeminiModelList(models=[], free_tier_only=g.free_tier_only, error=str(exc))
    found.sort(key=lambda m: (m["kind"] != "answer", not m["free_tier"], m["name"]))
    return GeminiModelList(models=[GeminiModel(**{k: m[k] for k in GeminiModel.model_fields}) for m in found],
                           free_tier_only=g.free_tier_only, paid_only=sum(not m["free_tier"] for m in found))


@router.put("/gemini/key", response_model=ActionResult)
async def save_gemini_key(body: GeminiKey, request: Request) -> ActionResult:
    """Check the key against the Gemini API, then save it in .env (git-ignored). The key is
    never written to config.yaml and never returned by the API."""
    s = request.app.state
    key = body.api_key.strip()
    if not _KEY_RE.fullmatch(key):
        raise HTTPException(422, "That does not look like a Gemini API key. Copy it from aistudio.google.com/api-keys.")
    try:
        await list_gemini_models(s.config, key, s.ollama_transport)
    except OllamaError as exc:
        raise HTTPException(422, f"Not saved: {exc}") from exc
    try:
        path = set_env_value(GEMINI_KEY_VARS[0], key)
    except (EnvError, OSError) as exc:
        raise HTTPException(500, f"Could not write the .env file: {exc}") from exc
    _reload(request)
    return ActionResult(ok=True, message=f"API key checked and saved in {path.name}.")


@router.delete("/gemini/key", response_model=ActionResult)
async def delete_gemini_key(request: Request) -> ActionResult:
    try:
        set_env_value(GEMINI_KEY_VARS[0], None)
    except (EnvError, OSError) as exc:
        raise HTTPException(500, f"Could not write the .env file: {exc}") from exc
    cfg = _reload(request)
    info = _gemini_info(cfg)
    if info.api_key_set:
        return ActionResult(ok=True, message=f"Removed from .env, but {info.api_key_source} is still set in the "
                                             "environment, so that key is still used.")
    return ActionResult(ok=True, message="API key removed from .env.")
