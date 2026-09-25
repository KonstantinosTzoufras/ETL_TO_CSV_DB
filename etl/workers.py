"""ETL_WORKERS registry: administrator-owned, same JSON-in-env-var pattern
as ETL_QUERY_CONNECTIONS/ETL_EXPORT_CONNECTIONS. Never supplied by HTTP/spec.
"""
import json
import os
import re

from .spec import ConfigError, require


def worker_registry():
    raw = os.environ.get("ETL_WORKERS", "{}")
    try:
        config = json.loads(raw)
    except (ValueError, TypeError):
        raise ConfigError("Invalid ETL_WORKERS: must be a JSON object") from None
    require(isinstance(config, dict), "ETL_WORKERS must be a JSON object")
    for name, value in config.items():
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)), f"Invalid worker name: {name}")
        require(isinstance(value, dict) and set(value) == {"url", "token"}, f"Worker '{name}' needs exactly url and token")
        require(isinstance(value["url"], str) and bool(re.fullmatch(r"https?://\S+", value["url"])), f"Worker '{name}' has an invalid url")
        require(isinstance(value["token"], str) and value["token"], f"Worker '{name}' needs a non-empty token")
    return config
