"""Build the web preview of the FinSight report: one self-contained HTML file.

The Power BI report is the primary deliverable; this page exists so the same four views can
be opened by anyone with a browser. It reads the same mart views and warehouse tables,
embeds the aggregated results as JSON and draws its own charts. No server, no external
requests.

    python -m dashboard.build --out site/index.html
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from etl.db import connect, load_settings

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__FINSIGHT_DATA__*/null"

# Every breakdown is produced once per calendar year and once for all years ("All"),
# using GROUPING SETS, so the page can switch year without recomputing anything.
QUERIES: dict[str, str] = {
    "monthly": """
        select d.year_month as month, d.year,
               sum(s.transactions) as transactions, sum(s.approved_transactions) as approved,
               sum(s.captured_transactions) as captured, sum(s.tpv) as tpv, sum(s.fee_revenue) as fees,
               sum(s.processing_cost) as cost, sum(s.refund_amount) as refunds, sum(s.refund_count) as refund_count,
               sum(s.chargeback_amount) as chargebacks, sum(s.chargeback_count) as chargeback_count,
               sum(s.chargeback_losses) as chargeback_losses,
               count(distinct m.merchant_id) filter (where s.captured_transactions > 0 and m.merchant_key <> -1) as active_merchants
        from mart.daily_merchant_summary s
        join dwh.dim_date d using (date_key)
        join dwh.dim_merchant m using (merchant_key)
        group by d.year_month, d.year order by d.year_month""",
    "countries": """
        select coalesce(d.year::text, 'All') as year, g.country_name as name, g.region, sum(f.amount_reporting) as tpv
        from dwh.fact_transaction f join dwh.dim_date d using (date_key) join dwh.dim_geography g using (geography_key)
        where f.is_captured and f.geography_key <> -1
        group by grouping sets ((d.year, g.country_name, g.region), (g.country_name, g.region))
        order by 1, tpv desc""",
    "categories": """
        select coalesce(d.year::text, 'All') as year, m.category as name, sum(s.tpv) as tpv,
               sum(s.fee_revenue) as fees, count(distinct m.merchant_id) as merchants
        from mart.daily_merchant_summary s join dwh.dim_date d using (date_key) join dwh.dim_merchant m using (merchant_key)
        where m.merchant_key <> -1
        group by grouping sets ((d.year, m.category), (m.category)) order by 1, tpv desc""",
    "merchants": """
        with yearly as (
            select m.merchant_id, d.year, sum(s.tpv) as tpv, sum(s.fee_revenue) as fees,
                   sum(s.refund_amount) as refunds, sum(s.transactions) as transactions,
                   sum(s.approved_transactions) as approved
            from mart.daily_merchant_summary s join dwh.dim_date d using (date_key)
            join dwh.dim_merchant m using (merchant_key)
            where m.merchant_key <> -1
            group by m.merchant_id, d.year
        ),
        ranked as (
            select y.*, lag(y.tpv) over (partition by y.merchant_id order by y.year) as tpv_py,
                   lag(y.year) over (partition by y.merchant_id order by y.year) as previous_year,
                   rank() over (partition by y.year order by y.tpv desc) as tpv_rank,
                   sum(y.tpv) over (partition by y.year) as year_total
            from yearly y
        )
        select r.year::text as year, r.tpv_rank as rank, c.merchant_name as name, c.category, c.country, c.risk_tier,
               c.pricing_plan, c.status, r.tpv, case when r.previous_year = r.year - 1 then r.tpv_py end as tpv_py,
               r.fees, r.refunds, r.transactions, r.approved, round(100 * r.tpv / nullif(r.year_total, 0), 3) as share
        from ranked r
        join mart.dim_merchant c on c.merchant_id = r.merchant_id and c.is_current
        order by r.year, r.tpv_rank""",
    "methods": """
        select coalesce(d.year::text, 'All') as year, p.payment_method as name, count(*) as transactions,
               count(*) filter (where f.is_approved) as approved,
               coalesce(sum(f.amount_reporting) filter (where f.is_captured), 0) as tpv
        from dwh.fact_transaction f join dwh.dim_date d using (date_key) join dwh.dim_payment_method p using (method_key)
        group by grouping sets ((d.year, p.payment_method), (p.payment_method)) order by 1, transactions desc""",
    "brands": """
        select coalesce(d.year::text, 'All') as year, p.card_brand as name, count(*) as transactions,
               count(*) filter (where f.is_approved) as approved
        from dwh.fact_transaction f join dwh.dim_date d using (date_key) join dwh.dim_payment_method p using (method_key)
        where p.card_brand <> 'Not Applicable'
        group by grouping sets ((d.year, p.card_brand), (p.card_brand)) order by 1, transactions desc""",
    "channels": """
        select coalesce(d.year::text, 'All') as year, c.channel_name as name, count(*) as transactions,
               count(*) filter (where f.is_approved) as approved
        from dwh.fact_transaction f join dwh.dim_date d using (date_key) join dwh.dim_channel c using (channel_key)
        group by grouping sets ((d.year, c.channel_name), (c.channel_name)) order by 1, transactions desc""",
    "declines": """
        select coalesce(d.year::text, 'All') as year, s.decline_reason as name, count(*) as transactions
        from dwh.fact_transaction f join dwh.dim_date d using (date_key) join dwh.dim_transaction_status s using (status_key)
        where not f.is_approved
        group by grouping sets ((d.year, s.decline_reason), (s.decline_reason)) order by 1, transactions desc""",
    "heatmap": """
        select coalesce(d.year::text, 'All') as year, d.day_of_week as dow, f.hour_of_day as hour, count(*) as transactions
        from dwh.fact_transaction f join dwh.dim_date d using (date_key)
        group by grouping sets ((d.year, d.day_of_week, f.hour_of_day), (d.day_of_week, f.hour_of_day))
        order by 1, 2, 3""",
    "chargeback_reasons": """
        select coalesce(d.year::text, 'All') as year, c.reason_code as name, count(*) as disputes,
               count(*) filter (where c.outcome = 'LOST') as lost, sum(c.chargeback_amount) as amount
        from dwh.fact_chargeback c join dwh.dim_date d using (date_key)
        group by grouping sets ((d.year, c.reason_code), (c.reason_code)) order by 1, disputes desc""",
    "settlement_monthly": """
        select d.year_month as month, d.year, count(*) as batches, count(*) filter (where not s.is_reconciled) as unreconciled,
               sum(s.expected_payout) as expected, sum(s.net_payout) as actual,
               sum(abs(s.variance)) filter (where not s.is_reconciled) as absolute_variance
        from dwh.fact_settlement s join dwh.dim_date d using (date_key)
        group by d.year_month, d.year order by d.year_month""",
    "unreconciled": """
        select s.settlement_batch_id as batch, d.full_date as date, d.year::text as year, m.merchant_name as merchant,
               c.currency_code as currency, s.expected_payout_original as expected, s.net_payout_original as actual,
               s.net_payout_original - s.expected_payout_original as variance, s.variance as variance_usd
        from dwh.fact_settlement s join dwh.dim_date d using (date_key) join dwh.dim_merchant m using (merchant_key)
        join dwh.dim_currency c using (currency_key)
        where not s.is_reconciled
        order by abs(s.variance) desc limit 200""",
    "rejections": """
        select entity, rule_name as rule, max(reason) as reason, sum(rejected_rows) as rows
        from mart.etl_rejections group by entity, rule_name order by rows desc""",
    "batches": """
        select batch_id as batch, rows_delivered, rows_rejected, controls_run, controls_passed, duration_ms
        from mart.etl_batches order by batch_id""",
}

TOTALS = """
    select
        (select count(*) from dwh.fact_transaction)                              as transactions,
        (select count(distinct merchant_id) from dwh.dim_merchant where merchant_key <> -1) as merchants,
        (select count(*) from dwh.dim_merchant where merchant_key <> -1)         as merchant_versions,
        (select count(*) from dwh.dim_customer where customer_key <> -1)         as customers,
        (select count(*) from dwh.fact_settlement)                               as settlement_batches,
        (select count(*) from dwh.fact_settlement where not is_reconciled)       as unreconciled_batches,
        (select min(full_date) from dwh.fact_transaction join dwh.dim_date using (date_key)) as first_date,
        (select max(full_date) from dwh.fact_transaction join dwh.dim_date using (date_key)) as last_date,
        (select count(*) from audit.etl_run where status = 'SUCCESS')            as batches_loaded,
        (select coalesce(sum(row_count), 0) from audit.etl_file f join audit.etl_run e using (run_id)
          where e.status = 'SUCCESS')                                            as rows_delivered,
        (select count(*) from audit.rejected_rows)                               as rows_rejected,
        (select count(*) from mart.reconciliation_controls)                      as controls_run,
        (select count(*) from mart.reconciliation_controls where status = 'PASS') as controls_passed,
        (select count(*) from dwh.fact_transaction where merchant_key = -1)      as unknown_merchant_payments,
        (select count(*) from dwh.fact_transaction where customer_key = -1)      as unknown_customer_payments,
        (select max(finished_at) from audit.etl_run where status = 'SUCCESS')    as last_load"""


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _rows(conn: psycopg.Connection, sql: str) -> list[dict]:
    cursor = conn.execute(sql)
    columns = [c.name for c in cursor.description]
    return [{k: _plain(v) for k, v in zip(columns, row, strict=True)} for row in cursor.fetchall()]


def collect(conn: psycopg.Connection) -> dict:
    data = {name: _rows(conn, sql) for name, sql in QUERIES.items()}
    data["totals"] = _rows(conn, TOTALS)[0]
    data["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return data


def build(conn: psycopg.Connection, out_path: Path) -> Path:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError("The dashboard template has no data placeholder")
    payload = json.dumps(collect(conn), separators=(",", ":")).replace("</", "<\\/")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(template.replace(PLACEHOLDER, payload), encoding="utf-8", newline="\n")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the web preview of the FinSight report")
    parser.add_argument("--out", type=Path, default=Path("site/index.html"))
    args = parser.parse_args(argv)
    with connect(load_settings().database_url, autocommit=True) as conn:
        path = build(conn, args.out)
    print(f"Dashboard written to {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
