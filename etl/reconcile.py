"""Reconcile: prove that what was delivered is what was loaded.

For every file of a batch, three controls:

* delivered   the row count in the manifest equals the rows landed in staging
* accounted   rows landed = rows loaded + rows quarantined (nothing vanished)
* amount      the total of the validated source rows equals the total in the warehouse

A failed control fails the run, and the run's transaction is rolled back. The results are
written through a separate connection so they survive that rollback.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import psycopg

from etl.extract import SourceFile

# entity -> (query for rows now in the warehouse for this batch, query for the warehouse control total)
# Dimension-like entities are checked by presence: every validated key must exist in the warehouse.
TARGETS: dict[str, tuple[str, str | None, str | None]] = {
    "merchants": (
        "select count(*) from staging.merchants_clean c "
        "where exists (select 1 from dwh.dim_merchant d where d.merchant_id = c.merchant_id)", None, None),
    "customers": (
        "select count(*) from staging.customers_clean c "
        "where exists (select 1 from dwh.dim_customer d where d.customer_hash = c.customer_hash)", None, None),
    "fx_rates": (
        "select count(*) from staging.fx_rates_clean c join dwh.dim_currency k on k.currency_code = c.currency_code "
        "where exists (select 1 from dwh.fx_rate r where r.currency_key = k.currency_key and r.rate_date = c.rate_date)",
        None, None),
    "transactions": (
        "select count(*) from dwh.fact_transaction where batch_id = %(batch_id)s",
        "select coalesce(sum(amount), 0) from staging.transactions_clean",
        "select coalesce(sum(amount_original), 0) from dwh.fact_transaction where batch_id = %(batch_id)s"),
    "refunds": (
        "select count(*) from dwh.fact_refund where batch_id = %(batch_id)s",
        "select coalesce(sum(amount), 0) from staging.refunds_clean",
        "select coalesce(sum(refund_original), 0) from dwh.fact_refund where batch_id = %(batch_id)s"),
    "chargebacks": (
        "select count(*) from dwh.fact_chargeback where batch_id = %(batch_id)s",
        "select coalesce(sum(amount), 0) from staging.chargebacks_clean",
        "select coalesce(sum(chargeback_original), 0) from dwh.fact_chargeback where batch_id = %(batch_id)s"),
    "fees": (
        "select count(*) from dwh.fact_fee where batch_id = %(batch_id)s",
        "select coalesce(sum(merchant_fee), 0) from staging.fees_clean",
        "select coalesce(sum(fee_original), 0) from dwh.fact_fee where batch_id = %(batch_id)s"),
    "settlements": (
        "select count(*) from dwh.fact_settlement where batch_id = %(batch_id)s",
        "select coalesce(sum(net_payout), 0) from staging.settlements_clean",
        "select coalesce(sum(net_payout_original), 0) from dwh.fact_settlement where batch_id = %(batch_id)s"),
}


@dataclass(frozen=True)
class Control:
    entity: str
    check_name: str
    source_value: Decimal
    target_value: Decimal

    @property
    def passed(self) -> bool:
        return self.source_value == self.target_value

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


class ReconciliationError(RuntimeError):
    def __init__(self, failures: list[Control]) -> None:
        self.failures = failures
        detail = "; ".join(f"{c.entity}.{c.check_name}: source {c.source_value}, warehouse {c.target_value}"
                           for c in failures)
        super().__init__(f"Reconciliation failed: {detail}")


def controls_for(entity: str, declared_rows: int, staged_rows: int, loaded_rows: int, rejected_rows: int,
                 source_total: Decimal | None, target_total: Decimal | None) -> list[Control]:
    """The controls for one file, from the figures collected for it. Pure: no database access."""
    controls = [
        Control(entity, "rows_delivered_vs_staged", Decimal(declared_rows), Decimal(staged_rows)),
        Control(entity, "rows_staged_vs_loaded_plus_rejected", Decimal(staged_rows),
                Decimal(loaded_rows + rejected_rows)),
    ]
    if source_total is not None and target_total is not None:
        controls.append(Control(entity, "amount_control_total", Decimal(source_total), Decimal(target_total)))
    return controls


def reconcile(conn: psycopg.Connection, batch_id: str, files: list[SourceFile],
              rejected: dict[str, dict[str, int]]) -> list[Control]:
    """Collect the figures for every file of the batch and build its controls."""
    params = {"batch_id": batch_id}
    controls: list[Control] = []
    for source in files:
        loaded_sql, source_total_sql, target_total_sql = TARGETS[source.entity]
        loaded = conn.execute(loaded_sql, params if "%(batch_id)s" in loaded_sql else None).fetchone()[0]
        source_total = conn.execute(source_total_sql).fetchone()[0] if source_total_sql else None
        target_total = conn.execute(target_total_sql, params).fetchone()[0] if target_total_sql else None
        controls.extend(controls_for(source.entity, source.declared_rows, source.staged_rows, loaded,
                                     sum(rejected.get(source.entity, {}).values()), source_total, target_total))
    return controls


def record(audit_conn: psycopg.Connection, run_id: int, batch_id: str, controls: list[Control]) -> None:
    with audit_conn.cursor() as cursor:
        cursor.executemany(
            """insert into audit.reconciliation (run_id, batch_id, entity, check_name, source_value, target_value, status)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, batch_id, c.entity, c.check_name, c.source_value, c.target_value, c.status) for c in controls],
        )
