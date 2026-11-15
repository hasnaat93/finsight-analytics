"""Measure the effect of the warehouse's indexes and its pre-aggregated mart.

    python -m etl.benchmark

For each case the same query is timed twice with EXPLAIN (ANALYZE): once as the warehouse
is, and once with the index removed (inside a transaction that is rolled back, so nothing
is changed) or against the raw facts instead of the mart. Each timing is the median of five
executions. The figures in docs/performance.md come from this script.
"""

from __future__ import annotations

import json
import statistics

import psycopg

from etl.db import connect, load_settings

RUNS = 5


def measure(conn: psycopg.Connection, sql: str) -> tuple[float, str]:
    """Median execution time in milliseconds, and the plan's top access method."""
    times, node = [], ""
    for _ in range(RUNS):
        plan = conn.execute("explain (analyze, format json) " + sql).fetchone()[0]
        plan = plan if isinstance(plan, list) else json.loads(plan)
        times.append(plan[0]["Execution Time"])
        node = _scan_node(plan[0]["Plan"])
    return statistics.median(times), node


def _scan_node(plan: dict) -> str:
    """The scan at the bottom of the plan: this is what an index changes."""
    while plan.get("Plans"):
        plan = plan["Plans"][0]
    name = plan["Node Type"]
    return f"{name} using {plan['Index Name']}" if "Index Name" in plan else name


def without_index(conn: psycopg.Connection, index: str, sql: str) -> tuple[float, str]:
    with conn.transaction(force_rollback=True):
        conn.execute(f"drop index dwh.{index}")
        return measure(conn, sql)


def main() -> int:
    with connect(load_settings().database_url) as conn:
        conn.execute("analyze")
        merchant_key, month_start, month_end = conn.execute("""
            select merchant_key, min(date_key), max(date_key) from dwh.fact_transaction
            where batch_id = (select max(batch_id) from dwh.fact_transaction)
              and merchant_key <> -1
            group by merchant_key order by count(*) desc limit 1""").fetchone()
        rows = conn.execute("select count(*) from dwh.fact_transaction").fetchone()[0]
        settlements = conn.execute("select count(*) from dwh.fact_settlement").fetchone()[0]

        one_merchant = f"""
            select count(*), sum(amount_reporting) from dwh.fact_transaction
            where merchant_key = {merchant_key} and date_key between {month_start} and {month_end}"""
        unreconciled = """
            select settlement_batch_id, variance from dwh.fact_settlement
            where not is_reconciled and date_key >= (select max(date_key) - 100 from dwh.fact_settlement)"""
        monthly_from_mart = """
            select d.year_month, sum(s.tpv), sum(s.fee_revenue), sum(s.refund_amount)
            from mart.daily_merchant_summary s join dwh.dim_date d using (date_key) group by d.year_month"""
        monthly_from_facts = """
            select d.year_month,
                   sum(f.amount_reporting) filter (where f.is_captured),
                   (select sum(e.fee_amount) from dwh.fact_fee e join dwh.dim_date x using (date_key)
                     where x.year_month = d.year_month),
                   (select sum(r.refund_amount) from dwh.fact_refund r join dwh.dim_date x using (date_key)
                     where x.year_month = d.year_month)
            from dwh.fact_transaction f join dwh.dim_date d using (date_key) group by d.year_month"""

        cases = [
            ("One merchant, one month of payments", "index fact_transaction_merchant (merchant_key, date_key)",
             without_index(conn, "fact_transaction_merchant", one_merchant), measure(conn, one_merchant)),
            ("Recent unreconciled settlement batches", "partial index fact_settlement_unreconciled",
             without_index(conn, "fact_settlement_unreconciled", unreconciled), measure(conn, unreconciled)),
            ("Monthly value, fees and refunds", "materialized view mart.daily_merchant_summary",
             measure(conn, monthly_from_facts), measure(conn, monthly_from_mart)),
        ]
        conn.rollback()

    print(f"fact_transaction rows: {rows:,}   fact_settlement rows: {settlements:,}   median of {RUNS} runs\n")
    print("| Query | Optimisation | Before | After | Speed-up | Access before | Access after |")
    print("|---|---|---|---|---|---|---|")
    for name, change, (before, before_node), (after, after_node) in cases:
        print(f"| {name} | {change} | {before:.1f} ms | {after:.2f} ms | {before / max(after, 0.001):.0f}x "
              f"| {before_node} | {after_node} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
