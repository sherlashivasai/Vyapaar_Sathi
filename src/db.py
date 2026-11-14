from __future__ import annotations

from pathlib import Path

import logfire
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "vyapar_saathi.db"
APP_DB_URL = f"sqlite:///{DB_PATH.as_posix()}"

Base = declarative_base()
engine = create_engine(APP_DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_sqlalchemy_instrumented = False


def get_engine():
    return engine


def init_db() -> None:
    global _sqlalchemy_instrumented

    from src import models  # noqa: F401

    if not _sqlalchemy_instrumented:
        logfire.instrument_sqlalchemy(engine)
        _sqlalchemy_instrumented = True

    with logfire.span("Initialize merchant database", database_path=str(DB_PATH)):
        Base.metadata.create_all(bind=engine)
        logfire.info(
            "Merchant database initialized",
            database_path=str(DB_PATH),
            database_url=APP_DB_URL,
            tables=sorted(Base.metadata.tables),
        )
