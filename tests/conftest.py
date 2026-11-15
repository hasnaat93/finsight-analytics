"""Test fixtures.

Unit tests need nothing. Integration tests need a PostgreSQL database they are allowed to
wipe, named in FINSIGHT_TEST_DATABASE_URL; without it they are skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from data_generator.generate_data import GeneratorConfig, generate, write_batch
from etl import run_pipeline
from etl.db import ROOT, Settings, connect, init_schema

TEST_DATABASE_URL = os.environ.get("FINSIGHT_TEST_DATABASE_URL", "")
needs_database = pytest.mark.skipif(not TEST_DATABASE_URL, reason="FINSIGHT_TEST_DATABASE_URL is not set")


def reset_database() -> None:
    with connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("drop schema if exists staging, dwh, mart, audit cascade")
    with connect(TEST_DATABASE_URL) as conn:
        init_schema(conn)


class Lab:
    """A scratch raw folder and a clean warehouse for hand-built batches."""

    def __init__(self, raw_dir: Path) -> None:
        self.settings = Settings(TEST_DATABASE_URL, raw_dir, ROOT / "config" / "validation_rules.yaml")
        self.conn = connect(TEST_DATABASE_URL, autocommit=True)

    def deliver(self, batch_id: str, **files: list[list[str]]) -> Path:
        content = {f"{name}.csv": [list(map(str, row)) for row in rows] for name, rows in files.items()}
        content.setdefault("fx_rates.csv", fx_rows(batch_id))
        return write_batch(self.settings.raw_dir, batch_id, content)

    def run(self, batch_id: str, reload: bool = False) -> run_pipeline.BatchResult:
        return run_pipeline.run_batch(self.settings, batch_id, reload=reload)

    def load(self, batch_id: str, **files: list[list[str]]) -> run_pipeline.BatchResult:
        self.deliver(batch_id, **files)
        result = self.run(batch_id)
        assert result.status == "SUCCESS", result.error
        return result

    def all(self, sql: str) -> list[tuple]:
        return self.conn.execute(sql).fetchall()

    def one(self, sql: str) -> tuple:
        return self.conn.execute(sql).fetchone()

    def value(self, sql: str):
        return self.conn.execute(sql).fetchone()[0]

    def rejected(self) -> dict[tuple[str, str], int]:
        return {(e, r): n for e, r, n in self.all(
            "select entity, rule_name, count(*) from audit.rejected_rows group by entity, rule_name")}


def fx_rows(batch_id: str, eur: str = "1.10", gbp: str = "1.25", pkr: str = "0.0036") -> list[list[str]]:
    """One rate per currency on the first day of the month; later days use it as the latest known rate."""
    day = f"{batch_id}-01"
    return [[day, "USD", "1"], [day, "EUR", eur], [day, "GBP", gbp], [day, "PKR", pkr]]


def merchant(merchant_id: str = "M1", name: str = "Alpha Store", *, country: str = "US", category: str = "Retail",
             risk: str = "LOW", plan: str = "STANDARD", status: str = "ACTIVE") -> list[str]:
    return [merchant_id, name, "5311", category, country, "2023-01-15", risk, plan, status]


def txn(txn_id: str, ts: str, *, merchant: str = "M1", customer: str = "c1", amount: str = "100.00",
        currency: str = "USD", method: str = "CARD", brand: str = "VISA", card_type: str = "CREDIT",
        channel: str = "ECOMMERCE", status: str = "CAPTURED", reason: str = "") -> list[str]:
    return [txn_id, merchant, customer, ts, amount, currency, method, brand, card_type, channel, status, reason]


def fee(txn_id: str, merchant_fee: str = "3.20", interchange: str = "1.50", scheme: str = "0.12",
        currency: str = "USD") -> list[str]:
    return [txn_id, merchant_fee, interchange, scheme, currency]


@pytest.fixture
def lab(tmp_path: Path):
    reset_database()
    lab = Lab(tmp_path / "raw")
    yield lab
    lab.conn.close()


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """A small generated data set (12 months), fully loaded. Shared and read-only."""
    reset_database()
    raw = tmp_path_factory.mktemp("generated")
    result = generate(GeneratorConfig(raw, months=12, scale=0.05, seed=42))
    lab = Lab(raw)
    lab.results = run_pipeline.run_pending(lab.settings)
    lab.conn.execute("refresh materialized view mart.daily_merchant_summary")
    lab.generation = result
    yield lab
    lab.conn.close()
