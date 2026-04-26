from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_model_env(model_name: str) -> Path:
    env_path = Path(__file__).resolve().parent / f".env.{model_name}"
    load_dotenv(env_path, override=False)
    return env_path


def optional_path(name: str, default: str | None = None) -> Path | None:
    value = os.getenv(name, default)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        raise RuntimeError(f"{name} does not point to a file: {path}")
    return path

