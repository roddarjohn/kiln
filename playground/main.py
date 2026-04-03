"""Playground FastAPI application.

Imports all generated routers and mounts them under /v1/.

Run with:
    just serve          (from playground/)
    just rg && just serve
"""

from __future__ import annotations

from fastapi import FastAPI
from _generated.routes import router

app = FastAPI(
    title="Kiln Playground",
    description="Generated blog + inventory API.",
    version="0.1.0",
)

app.include_router(router, prefix="/v1")
