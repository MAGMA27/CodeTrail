"""Persistent server configuration for model providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


MODEL_CONFIG_FILENAME = "config.json"
DEFAULT_MODEL_PROVIDER = "deepseek"
DEFAULT_MODEL_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_NAME = "deepseek-v4-flash"


class ModelConfigError(ValueError):
    """Raised when the persistent model configuration cannot be loaded safely."""


def normalize_model_config_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def normalize_model_config_base_url(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("base_url must be an absolute HTTP(S) URL without credentials")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url must be an absolute HTTP(S) URL without credentials") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("base_url must be an absolute HTTP(S) URL without credentials")
    return normalized


class ModelProviderConfig(BaseModel):
    """Server-owned credentials and endpoint settings for one model provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default=DEFAULT_MODEL_PROVIDER, min_length=1, max_length=100, pattern=r".*\S.*")
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(
        default=DEFAULT_MODEL_BASE_URL,
        min_length=1,
        max_length=2_000,
        json_schema_extra={"format": "uri", "pattern": r"^https?://", "description": "Absolute HTTP(S) URL without userinfo credentials."},
    )
    model: str = Field(default=DEFAULT_MODEL_NAME, min_length=1, max_length=200, pattern=r".*\S.*")

    @field_validator("provider", "model")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return normalize_model_config_text(value)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return normalize_model_config_base_url(value)


def model_config_path(data_root: str | Path) -> Path:
    """Return the service-owned model config path under the prepared data root."""

    return Path(data_root) / MODEL_CONFIG_FILENAME


def load_model_config(data_root: str | Path) -> ModelProviderConfig:
    """Load the optional persistent model config, defaulting to an empty DeepSeek config."""

    return load_model_config_file(model_config_path(data_root))


def load_model_config_file(path: str | Path) -> ModelProviderConfig:
    """Load one exact persistent config file without accepting environment secrets."""

    config_path = Path(path)
    try:
        raw = config_path.read_text(encoding="utf-8")
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


def save_model_config(data_root: str | Path, config: ModelProviderConfig) -> None:
    """Atomically replace the service-owned model config without exposing its contents."""

    path = model_config_path(data_root)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".config-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(config.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        raise ModelConfigError("persistent model config cannot be saved") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
