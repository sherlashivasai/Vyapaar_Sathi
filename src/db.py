from __future__ import annotations

from pathlib import Path

import logfire
from sqlalchemy import create_engine

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "vyapar_saathi.db"
APP_DB_URL = f"sqlite:///{DB_PATH.as_posix()}"


def get_engine():
    engine = create_engine(APP_DB_URL, connect_args={"check_same_thread": False})
    try:
        logfire.instrument_sqlalchemy(engine)
    except Exception:
        pass
    logfire.info(
        "Database engine initialized",
        database_path=str(DB_PATH),
        database_url=APP_DB_URL,
    )
    return engine
