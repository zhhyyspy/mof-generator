"""Application configuration via environment variables."""
import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    host: str = "0.0.0.0"
    port: int = 8000

    base_dir: Path = Path(__file__).resolve().parent
    data_dir: Path = base_dir / "data"
    documents_dir: Path = data_dir / "documents"
    models_dir: Path = data_dir / "models"
    m2_templates_dir: Path = data_dir / "m2_templates"
    exports_dir: Path = data_dir / "exports"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure data dirs exist
for d in [settings.documents_dir, settings.models_dir,
          settings.m2_templates_dir, settings.exports_dir]:
    d.mkdir(parents=True, exist_ok=True)
