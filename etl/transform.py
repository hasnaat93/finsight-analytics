"""Transform: batch periods and the cleanse-and-conform step for reference data.

Heavy transformation is done in SQL inside the database (sql/dml), where the data already
is. Python decides what runs and with which parameters.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import psycopg

from etl.db import run_script

_BATCH_ID = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def batch_period(batch_id: str) -> tuple[date, date]:
    """First and last day of a monthly batch, from its id (yyyy-mm)."""
    match = _BATCH_ID.match(batch_id)
    if not match:
        raise ValueError(f"'{batch_id}' is not a batch id; expected yyyy-mm")
    year, month = int(match.group(1)), int(match.group(2))
    first = date(year, month, 1)
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    return first, next_first - timedelta(days=1)


def is_batch_id(name: str) -> bool:
    return bool(_BATCH_ID.match(name))


def conform_reference(conn: psycopg.Connection) -> None:
    """Build the typed merchants, customers and FX tables from the validated staging rows."""
    run_script(conn, "dml/01_conform_reference.sql")
