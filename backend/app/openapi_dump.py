"""Write the OpenAPI schema to frontend/openapi.json (for `npm run gen:types:file`).

    cd backend; .venv\\Scripts\\python -m app.openapi_dump
"""

from __future__ import annotations

import json

from .config import REPO_ROOT
from .main import create_app

if __name__ == "__main__":
    out = REPO_ROOT / "frontend" / "openapi.json"
    out.write_text(json.dumps(create_app().openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {out}")
