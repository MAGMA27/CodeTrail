"""Minimal CodeTrail API application."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Stable response contract for the local health check."""

    status: Literal["ok"]
    service: Literal["codetrail-api"]
    version: str


def _allowed_origins() -> list[str]:
    configured_origins = os.getenv(
        "CODETRAIL_WEB_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]


app = FastAPI(title="CodeTrail API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report that the local API is available."""

    return HealthResponse(status="ok", service="codetrail-api", version="0.1.0")
