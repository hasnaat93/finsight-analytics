"""Extract: verify a delivered batch and land it, unchanged, in the staging schema."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

import psycopg

# file name -> (entity, expected columns)
SOURCES: dict[str, tuple[str, list[str]]] = {
    "merchants.csv": ("merchants", ["merchant_id", "merchant_name", "mcc", "category", "country", "onboarding_date",
                                    "risk_tier", "pricing_plan", "status"]),
    "customers.csv": ("customers", ["customer_id", "country", "segment"]),
    "fx_rates.csv": ("fx_rates", ["rate_date", "currency", "rate_to_usd"]),
    "transactions.csv": ("transactions", ["transaction_id", "merchant_id", "customer_id", "transaction_ts", "amount",
                                          "currency", "payment_method", "card_brand", "card_type", "channel",
                                          "status", "decline_reason"]),
    "refunds.csv": ("refunds", ["refund_id", "transaction_id", "refund_ts", "amount", "currency", "reason"]),
    "chargebacks.csv": ("chargebacks", ["chargeback_id", "transaction_id", "chargeback_date", "amount", "currency",
                                        "reason_code", "outcome"]),
    "fees.csv": ("fees", ["transaction_id", "merchant_fee", "interchange_cost", "scheme_cost", "currency"]),
    "settlements.csv": ("settlements", ["settlement_batch_id", "merchant_id", "settlement_date", "currency",
                                        "gross_amount", "fee_amount", "refund_amount", "chargeback_amount",
                                        "net_payout"]),
}
ENTITIES = [entity for entity, _ in SOURCES.values()]


class ExtractError(RuntimeError):
    """The delivery is incomplete, altered, or not in the agreed layout."""


@dataclass(frozen=True)
class SourceFile:
    file_name: str
    entity: str
    declared_rows: int
    sha256: str
    staged_rows: int


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_header(payload: bytes) -> list[str]:
    first_line = payload.split(b"\n", 1)[0].decode("utf-8")
    return next(csv.reader(io.StringIO(first_line)), [])


def verify_batch(folder: Path, batch_id: str) -> dict[str, dict]:
    """Check the manifest against the files. Nothing is loaded unless the whole delivery is sound."""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise ExtractError(f"No manifest for batch {batch_id} at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("batch_id") != batch_id:
        raise ExtractError(f"Manifest is for batch {manifest.get('batch_id')}, expected {batch_id}")
    declared = manifest.get("files", {})
    for file_name, (_entity, columns) in SOURCES.items():
        path = folder / file_name
        if file_name not in declared or not path.is_file():
            raise ExtractError(f"{file_name} is missing from batch {batch_id}")
        payload = path.read_bytes()
        if sha256_of(payload) != declared[file_name]["sha256"]:
            raise ExtractError(f"{file_name}: checksum does not match the manifest (file changed or truncated)")
        header = read_header(payload)
        if header != columns:
            raise ExtractError(f"{file_name}: unexpected columns {header}; expected {columns}")
    return declared


def stage_batch(conn: psycopg.Connection, folder: Path, batch_id: str, run_id: int) -> list[SourceFile]:
    """Verify the batch, then bulk-load every file into its staging table with COPY."""
    declared = verify_batch(folder, batch_id)
    staged: list[SourceFile] = []
    for file_name, (entity, columns) in SOURCES.items():
        conn.execute(f"truncate table staging.{entity} restart identity")
        copy_sql = f"copy staging.{entity} ({', '.join(columns)}) from stdin with (format csv, header true)"
        with conn.cursor().copy(copy_sql) as copy, (folder / file_name).open("rb") as source:
            while chunk := source.read(1 << 20):
                copy.write(chunk)
        rows = conn.execute(f"select count(*) from staging.{entity}").fetchone()[0]
        info = declared[file_name]
        conn.execute(
            "insert into audit.etl_file (run_id, file_name, row_count, sha256) values (%s, %s, %s, %s)",
            (run_id, file_name, rows, info["sha256"]),
        )
        staged.append(SourceFile(file_name, entity, int(info["rows"]), info["sha256"], rows))
    return staged
