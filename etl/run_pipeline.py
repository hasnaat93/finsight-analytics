"""Run the FinSight ETL.

    python -m etl.run_pipeline init                 create or update the warehouse schema
    python -m etl.run_pipeline run                  load every batch not yet loaded, oldest first
    python -m etl.run_pipeline run --batch 2025-03  load one batch
    python -m etl.run_pipeline run --batch 2025-03 --reload
    python -m etl.run_pipeline test                 run the SQL assertions in sql/tests
    python -m etl.run_pipeline status               what is loaded, what was rejected

A batch is one month of source files. It loads in a single transaction: if any step fails,
or a reconciliation control does not balance, nothing of the batch reaches the warehouse.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from etl import extract, load_dimensions, load_facts, reconcile, transform, validate
from etl.db import SQL_DIR, Settings, connect, init_schema, load_settings

log = logging.getLogger("finsight.etl")
_PIPELINE_LOCK = 20231101          # advisory lock: the staging tables are shared, so one run at a time

REFERENCE = ["merchants", "customers", "fx_rates"]
DEPENDENTS = ["refunds", "chargebacks", "fees"]


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so a run can be followed or filtered by run_id and batch."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"time": datetime.now(UTC).isoformat(timespec="seconds"), "level": record.levelname,
                 "message": record.getMessage()}
        entry.update(getattr(record, "context", {}))
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


@dataclass
class BatchResult:
    batch_id: str
    status: str                                   # SUCCESS | FAILED | SKIPPED
    run_id: int | None = None
    duration_ms: int = 0
    rows_staged: int = 0
    rejected: dict[str, dict[str, int]] = field(default_factory=dict)
    controls: list[reconcile.Control] = field(default_factory=list)
    merchant_history_applied: bool = True
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != "FAILED"

    @property
    def rows_rejected(self) -> int:
        return sum(sum(by_rule.values()) for by_rule in self.rejected.values())


def pending_batches(conn: psycopg.Connection, raw_dir: Path) -> list[str]:
    """Delivered batches that have no successful load yet, oldest first."""
    delivered = sorted(p.name for p in Path(raw_dir).iterdir() if p.is_dir() and transform.is_batch_id(p.name)) \
        if Path(raw_dir).is_dir() else []
    loaded = {row[0] for row in conn.execute("select batch_id from audit.etl_run where status = 'SUCCESS'")}
    return [b for b in delivered if b not in loaded]


def run_batch(settings: Settings, batch_id: str, reload: bool = False) -> BatchResult:
    """Load one batch. Never raises for a failed load; inspect the result."""
    batch_start, batch_end = transform.batch_period(batch_id)
    rules = validate.load_rules(settings.rules_file)
    started = time.perf_counter()
    result = BatchResult(batch_id, "FAILED")

    with connect(settings.database_url, autocommit=True) as audit, connect(settings.database_url) as conn:
        audit.execute("select pg_advisory_lock(%s)", (_PIPELINE_LOCK,))
        already = audit.execute(
            "select 1 from audit.etl_run where batch_id = %s and status = 'SUCCESS'", (batch_id,)).fetchone()
        if already and not reload:
            return BatchResult(batch_id, "SKIPPED")

        run_id = audit.execute(
            "insert into audit.etl_run (batch_id, status) values (%s, 'RUNNING') returning run_id", (batch_id,)
        ).fetchone()[0]
        result.run_id = run_id
        context = {"context": {"run_id": run_id, "batch_id": batch_id}}
        log.info("batch started", extra=context)

        def check(entities: list[str]) -> None:
            for entity in entities:
                rejected = validate.validate(conn, rules[entity], run_id, batch_id, batch_end)
                if rejected:
                    result.rejected[entity] = rejected

        try:
            with conn.transaction():
                # A reload replaces the earlier successful run of this batch and what it recorded.
                conn.execute("delete from audit.etl_run where batch_id = %s and status = 'SUCCESS'", (batch_id,))

                files = extract.stage_batch(conn, settings.raw_dir / batch_id, batch_id, run_id)
                result.rows_staged = sum(f.staged_rows for f in files)

                check(REFERENCE)
                transform.conform_reference(conn)
                result.merchant_history_applied = load_dimensions.load_dimensions(conn, batch_id, batch_start, batch_end)

                check(["transactions"])
                load_facts.load_transactions(conn, batch_id)
                check(DEPENDENTS)
                load_facts.load_dependents(conn, batch_id)
                check(["settlements"])
                load_facts.load_settlements(conn, batch_id)

                result.controls = reconcile.reconcile(conn, batch_id, files, result.rejected)
                reconcile.record(audit, run_id, batch_id, result.controls)
                failures = [c for c in result.controls if not c.passed]
                if failures:
                    raise reconcile.ReconciliationError(failures)

                result.duration_ms = int((time.perf_counter() - started) * 1000)
                conn.execute(
                    """update audit.etl_run set status = 'SUCCESS', finished_at = now(), duration_ms = %s
                       where run_id = %s""", (result.duration_ms, run_id))
            result.status = "SUCCESS"
            log.info("batch loaded", extra={"context": {**context["context"], "rows_staged": result.rows_staged,
                                                        "rows_rejected": result.rows_rejected,
                                                        "duration_ms": result.duration_ms}})
        except Exception as exc:        # the transaction is already rolled back
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            result.error = str(exc).strip()[:2000]
            audit.execute(
                """update audit.etl_run set status = 'FAILED', finished_at = now(), duration_ms = %s, error = %s
                   where run_id = %s""", (result.duration_ms, result.error, run_id))
            log.error("batch failed", extra={"context": {**context["context"], "error": result.error}})
    return result


def run_pending(settings: Settings) -> list[BatchResult]:
    """Load every pending batch in order, stopping at the first failure."""
    with connect(settings.database_url, autocommit=True) as conn:
        batches = pending_batches(conn, settings.raw_dir)
    results = []
    for batch_id in batches:
        result = run_batch(settings, batch_id)
        results.append(result)
        if not result.ok:
            break
    return results


def run_sql_tests(conn: psycopg.Connection) -> list[tuple[str, int]]:
    """Run every assertion in sql/tests. Each file is a query that returns the rows violating a rule."""
    outcomes = []
    for path in sorted((SQL_DIR / "tests").glob("*.sql")):
        query = path.read_text(encoding="utf-8").strip().rstrip(";")
        violations = conn.execute(f"select count(*) from ({query}) as violations").fetchone()[0]
        outcomes.append((path.stem, violations))
    return outcomes


# ---------------------------------------------------------------------- CLI

def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    cells = [["" if v is None else str(v) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))


def cmd_init(settings: Settings, _args: argparse.Namespace) -> int:
    with connect(settings.database_url) as conn:
        init_schema(conn)
    print("Warehouse schema is ready")
    return 0


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    with connect(settings.database_url) as conn:
        init_schema(conn)
    results = [run_batch(settings, args.batch, reload=args.reload)] if args.batch else run_pending(settings)
    if not results:
        print("Nothing to load: every delivered batch is already in the warehouse")
        return 0
    _print_table(["batch", "status", "rows staged", "rejected", "seconds", "note"], [
        [r.batch_id, r.status, r.rows_staged, r.rows_rejected, f"{r.duration_ms / 1000:.1f}",
         r.error or ("" if r.merchant_history_applied else "merchant history not applied (older batch)")]
        for r in results])
    failed = [r for r in results if not r.ok]
    if failed:
        return 1
    if any(r.status == "SUCCESS" for r in results):
        with connect(settings.database_url, autocommit=True) as conn:
            conn.execute("refresh materialized view mart.daily_merchant_summary")
        print("Refreshed mart.daily_merchant_summary")
    return 0


def cmd_test(settings: Settings, _args: argparse.Namespace) -> int:
    with connect(settings.database_url, autocommit=True) as conn:
        outcomes = run_sql_tests(conn)
    _print_table(["assertion", "violations", "result"],
                 [[name, count, "PASS" if count == 0 else "FAIL"] for name, count in outcomes])
    failed = [name for name, count in outcomes if count]
    print(f"\n{len(outcomes) - len(failed)} of {len(outcomes)} SQL assertions passed")
    return 1 if failed else 0


def cmd_status(settings: Settings, _args: argparse.Namespace) -> int:
    with connect(settings.database_url, autocommit=True) as conn:
        print("Warehouse")
        tables = ["dim_merchant", "dim_customer", "fact_transaction", "fact_refund", "fact_chargeback", "fact_fee",
                  "fact_settlement"]
        _print_table(["table", "rows"], [[t, f"{conn.execute(f'select count(*) from dwh.{t}').fetchone()[0]:,}"]
                                         for t in tables])
        print("\nBatches")
        _print_table(["status", "batches", "first", "last"], [list(r) for r in conn.execute(
            "select status, count(*), min(batch_id), max(batch_id) from audit.etl_run group by status order by status")])
        print("\nQuarantined rows")
        _print_table(["entity", "rule", "rows"], [list(r) for r in conn.execute(
            """select entity, rule_name, count(*) from audit.rejected_rows
               group by entity, rule_name order by entity, count(*) desc""")])
        print("\nSettlement reconciliation")
        _print_table(["batches", "unreconciled", "reconciliation rate"], [list(r) for r in conn.execute(
            """select count(*), count(*) filter (where not is_reconciled),
                      round(100.0 * count(*) filter (where is_reconciled) / nullif(count(*), 0), 3) || ' pct'
               from dwh.fact_settlement""")])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m etl.run_pipeline", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create or update the warehouse schema").set_defaults(func=cmd_init)
    run = sub.add_parser("run", help="load pending batches, or one batch")
    run.add_argument("--batch", help="batch id, yyyy-mm")
    run.add_argument("--reload", action="store_true", help="load the batch again even if it is already loaded")
    run.set_defaults(func=cmd_run)
    sub.add_parser("test", help="run the SQL assertions").set_defaults(func=cmd_test)
    sub.add_parser("status", help="show what is loaded").set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    if getattr(args, "reload", False) and not args.batch:
        parser.error("--reload needs --batch")
    configure_logging()
    return args.func(load_settings(args.config), args)


if __name__ == "__main__":
    raise SystemExit(main())
