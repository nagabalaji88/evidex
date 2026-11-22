"""Register models that FastAPI cannot see (stream event payloads) in /openapi.json,
so openapi-typescript generates their types too (spec section 8.1)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel


def register_models(app: FastAPI, models: list[type[BaseModel]]) -> None:
    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for m in models:
            js = m.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, sub in js.pop("$defs", {}).items():
                components.setdefault(name, sub)
            components.setdefault(m.__name__, js)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
