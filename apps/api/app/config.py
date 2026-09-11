"""Persistent server configuration for model providers."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


MODEL_CONFIG_FILENAME = "config.json"
DEFAULT_MODEL_PROVIDER = "deepseek"
DEFAULT_MODEL_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_NAME = "deepseek-flash"


class ModelConfigError(ValueError):
    """Raised when the persistent model configuration cannot be loaded safely."""


class ModelProviderConfig(BaseModel):
    """Server-owned credentials and endpoint settings for one model provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default=DEFAULT_MODEL_PROVIDER, min_length=1, max_length=100)
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(default=DEFAULT_MODEL_BASE_URL, min_length=1, max_length=2_000)
    model: str = Field(default=DEFAULT_MODEL_NAME, min_length=1, max_length=200)


def model_config_path(data_root: str | Path) -> Path:
    """Return the service-owned model config path under the prepared data root."""

    return Path(data_root) / MODEL_CONFIG_FILENAME


def load_model_config(data_root: str | Path) -> ModelProviderConfig:
    """Load the optional persistent model config, defaulting to an empty DeepSeek config."""

    path = model_config_path(data_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ModelProviderConfig()
    except OSError as exc:
        raise ModelConfigError("persistent model config cannot be read") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelConfigError("persistent model config must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ModelConfigError("persistent model config must be a JSON object")
    try:
        return ModelProviderConfig.model_validate(decoded)
    except ValidationError as exc:
        raise ModelConfigError("persistent model config has invalid fields") from exc
