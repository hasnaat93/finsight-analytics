"""Load facts. Each load is an insert keyed on the business key, so repeating it is a no-op."""

from __future__ import annotations

import psycopg

from etl.db import run_script


def load_transactions(conn: psycopg.Connection, batch_id: str) -> None:
    run_script(conn, "dml/04_load_fact_transaction.sql", {"batch_id": batch_id})


def load_dependents(conn: psycopg.Connection, batch_id: str) -> None:
    """Refunds, chargebacks and fees: everything that hangs off a payment."""
    run_script(conn, "dml/05_load_fact_dependents.sql", {"batch_id": batch_id})


def load_settlements(conn: psycopg.Connection, batch_id: str) -> None:
    """Settlements, each compared with the payout the warehouse expects from its own facts."""
    run_script(conn, "dml/06_load_fact_settlement.sql", {"batch_id": batch_id})
