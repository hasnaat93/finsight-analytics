"""Configuration and database access shared by the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import yaml

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"


@dataclass(frozen=True)
class Settings:
    database_url: str
    raw_dir: Path
    rules_file: Path


def load_settings(config_file: Path | None = None) -> Settings:
    """Read config/config.yaml. Environment variables override the file, so no secret needs to be in it."""
    path = config_file or ROOT / "config" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    database = data.get("database", {})
    paths = data.get("paths", {})
    return Settings(
        database_url=os.environ.get("FINSIGHT_DATABASE_URL", database.get("url", "")),
        raw_dir=Path(os.environ.get("FINSIGHT_RAW_DIR", ROOT / paths.get("raw", "data/raw"))),
        rules_file=ROOT / paths.get("validation_rules", "config/validation_rules.yaml"),
    )


def connect(database_url: str, autocommit: bool = False) -> psycopg.Connection:
    if not database_url:
        raise RuntimeError("No database configured. Set FINSIGHT_DATABASE_URL or database.url in config/config.yaml.")
    return psycopg.connect(database_url, autocommit=autocommit)


def run_script(conn: psycopg.Connection, relative_path: str, params: dict[str, Any] | None = None) -> None:
    """Run every statement in a file under sql/.

    A file holds several statements. PostgreSQL only accepts that without server-side
    parameters, so the values are bound on the client (psycopg.ClientCursor), which
    quotes them exactly as server-side binding would treat them.
    """
    sql = (SQL_DIR / relative_path).read_text(encoding="utf-8")
    with psycopg.ClientCursor(conn) as cursor:
        cursor.execute(sql, params) if params else cursor.execute(sql)


def scalar(conn: psycopg.Connection, sql: str, params: dict[str, Any] | None = None) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def init_schema(conn: psycopg.Connection) -> None:
    """Create or update every schema object. Idempotent."""
    for folder in ("ddl", "mart"):
        for path in sorted((SQL_DIR / folder).glob("*.sql")):
            run_script(conn, f"{folder}/{path.name}")
    conn.commit()
