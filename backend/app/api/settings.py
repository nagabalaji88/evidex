"""GET/PUT /api/settings (read, validate and save config.yaml), GET /api/models (installed
Ollama models with tested and fits-in-RAM flags) and POST /api/reindex (whole library)."""

from __future__ import annotations

import psutil
from fastapi import APIRouter, HTTPException, Request

from ..config import ConfigError, config_path, get_config, load_config, parse_config
from ..config_writer import set_value
from ..ollama_client import OllamaError
from ..schemas import ActionResult, ModelInfo, ModelList, SettingsResponse, SettingsSaved, SettingsUpdate

router = APIRouter(prefix="/api")

# Spec section 8 (Settings): these changes show "Re-run the evaluation after saving".
RERUN_KEYS = {"models.answer", "models.embedding", "reranker.enabled", "reranker.model", "retrieval.relevance_threshold",
              "retrieval.vector_threshold", "retrieval.final_k", "ingestion.chunk_size_tokens",
              "ingestion.chunk_overlap_tokens", "ollama.num_ctx"}
# Embedding or chunk changes ask to re-index.
REINDEX_KEYS = {"models.embedding", "models.embedding_doc_prefix", "models.embedding_query_prefix",
                "ingestion.chunk_size_tokens", "ingestion.chunk_overlap_tokens"}
# Spec section 2 RAM budget: everything except the answer model (embeddings 0.3, reranker 1,
# Chroma/SQLite/FastAPI 1, browser 1, OS and other apps 4) is about 7.3 GB.
RESERVED_RAM_GB = 7.3


def _config_dict(cfg) -> dict:
    return cfg.model_dump(mode="json", exclude={"base_dir"})


def _path(request: Request):
    return getattr(request.app.state, "config_file", None) or config_path()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request) -> SettingsResponse:
    p = _path(request)
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    return SettingsResponse(config=_config_dict(request.app.state.config), yaml=text, path=str(p))


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
    p.write_text(text, encoding="utf-8")
    get_config.cache_clear()
    try:
        new_cfg = load_config(p).model_copy(update={"base_dir": s.config.base_dir})
    except ConfigError as exc:  # should not happen: the same values validated above
        raise HTTPException(500, str(exc)) from exc
    s.build_services(request.app, new_cfg)
    rerun = any(k in RERUN_KEYS for k in changed)
    reindex = any(k in REINDEX_KEYS for k in changed)
    msg = "Saved to config.yaml."
    if rerun:
        msg += " Re-run the evaluation after saving."
    if reindex:
        msg += " Re-index the documents so they use the new settings."
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
    s = request.app.state
    cfg = s.config
    try:
        installed = await s.ollama.list_models()
    except OllamaError as exc:
        return ModelList(models=[], default=cfg.models.answer, error=str(exc))
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
                             tested=name == cfg.models.answer,
                             fits_in_ram=size_gb is None or size_gb * 1.2 <= budget))
    out.sort(key=lambda x: (not x.tested, x.is_embedding, x.name))
    return ModelList(models=out, default=cfg.models.answer)
