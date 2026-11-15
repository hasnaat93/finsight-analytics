"""Load dimensions: calendar, exchange rates, customers and the type 2 merchant dimension."""

from __future__ import annotations

from datetime import date

import psycopg

from etl.db import run_script, scalar


def load_dimensions(conn: psycopg.Connection, batch_id: str, batch_start: date, batch_end: date) -> bool:
    """Upsert the dimensions for a batch. Returns False if the merchant history step was skipped.

    Merchant history can only be built forwards. When an older batch is reloaded after a
    newer one, its merchant file is a snapshot of the past and is not applied.
    """
    run_script(conn, "dml/02_load_dimensions.sql", {"batch_start": batch_start, "batch_end": batch_end})
    newest_loaded = scalar(conn, "select max(batch_id) from audit.etl_run where status = 'SUCCESS'")
    if newest_loaded is not None and batch_id < newest_loaded:
        return False
    run_script(conn, "dml/03_load_dim_merchant_scd2.sql", {"batch_start": batch_start})
    return True
