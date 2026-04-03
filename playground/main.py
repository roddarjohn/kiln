"""Playground FastAPI application.

Imports all generated routers and mounts them under /v1/.

Run with:
    just serve          (from playground/)
    just rg && just serve
"""

from __future__ import annotations

import sys
from pathlib import Path

# Both directories contribute to the same namespace packages via
# Python namespace packages (no __init__.py in blog/ or inventory/).
# playground/            → blog/, inventory/ (hand-written models and code)
# playground/_generated/ → blog/routes/, inventory/routes/, auth/, db/ (generated)
here = Path(__file__).parent
sys.path.insert(0, str(here))
sys.path.insert(0, str(here / "_generated"))

from fastapi import FastAPI
from routes import router  # _generated/routes/__init__.py

app = FastAPI(
    title="Kiln Playground",
    description="Generated blog + inventory API.",
    version="0.1.0",
)

app.include_router(router, prefix="/v1")
