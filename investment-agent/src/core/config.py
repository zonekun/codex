"""設定読み込みモジュール."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """環境変数ベースの設定."""

    anthropic_api_key: str = ""
    jquants_api_key: str = ""
    jquants_refresh_token: str = ""
    edinet_api_key: str = ""
    twitter_bearer_token: str = ""
    youtube_api_key: str = ""
    google_application_credentials: str = "keys/gcp-service-account.json"
    gcp_project_id: str = "gmailpj-357912"
    env: str = "development"
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """YAMLファイルを読み込んで辞書で返す."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


settings = Settings()
app_config = load_yaml_config("config/settings.yaml")
sources_config = load_yaml_config("config/sources.yaml")
broker_config = load_yaml_config("config/broker.yaml")
