"""Logic that needs no database: batch periods, rule parsing, SQL building, controls, generator."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import numpy as np
import pytest

from data_generator.generate_data import GeneratorConfig, fmt_money, generate, month_start
from etl import extract, reconcile, transform, validate
from etl.db import ROOT


def test_batch_period_covers_the_whole_month():
    assert transform.batch_period("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))      # leap year
    assert transform.batch_period("2025-02") == (date(2025, 2, 1), date(2025, 2, 28))
    assert transform.batch_period("2025-12") == (date(2025, 12, 1), date(2025, 12, 31))


@pytest.mark.parametrize("bad", ["2025-13", "2025-1", "25-01", "2025-00", "january", ""])
def test_malformed_batch_ids_are_refused(bad):
    assert not transform.is_batch_id(bad)
    with pytest.raises(ValueError):
        transform.batch_period(bad)


def test_the_shipped_rules_file_parses_and_covers_every_source_file():
    rules = validate.load_rules(ROOT / "config" / "validation_rules.yaml")
    assert set(rules) == set(extract.ENTITIES)
    assert [r.name for r in rules["transactions"].rules][:4] == [
        "missing_transaction_id", "invalid_timestamp", "future_timestamp", "non_positive_amount"]
    assert rules["transactions"].duplicates.name == "duplicate_transaction_id"
    for entity_rules in rules.values():
        assert entity_rules.duplicates is not None
        for rule in entity_rules.rules:
            assert "\n" not in rule.when and rule.reason


@pytest.mark.parametrize(("document", "message"), [
    ({"transactions": {"rules": [{"name": "x", "when": "true"}]}}, "missing"),
    ({"transactions": {"rules": [{"name": "Bad Name", "reason": "r", "when": "true"}]}}, "not a valid rule name"),
    ({"transactions": {"rules": [{"name": "a", "reason": "r", "when": "true"},
                                 {"name": "a", "reason": "r", "when": "false"}]}}, "defined twice"),
    ({"drop table x": {"rules": []}}, "not a valid entity name"),
    ({"transactions": {"duplicates": {"name": "d", "reason": "r", "key": "id"}}}, "missing"),
])
def test_malformed_rule_files_are_refused(document, message):
    with pytest.raises(validate.RuleConfigError, match=message):
        validate.parse_rules(document)


def test_a_rule_only_touches_rows_that_are_not_already_rejected():
    sql = validate.rule_sql("transactions", validate.Rule("future_timestamp", "r", "ts >= :batch_end + 1"))
    assert sql.startswith("update staging.transactions_work set rule = %(rule_name)s")
    assert "where rule is null and (ts >= cast(%(batch_end)s as date) + 1)" in sql


def test_the_duplicate_rule_keeps_the_first_row_of_each_group():
    sql = validate.duplicate_sql("refunds", validate.DuplicateRule("dup", "r", "trim(refund_id)", "row_number desc"))
    assert "partition by trim(refund_id) order by row_number desc" in sql
    assert "ranked.position > 1" in sql and "where rule is null" in sql


def test_controls_balance_only_when_every_row_and_cent_is_accounted_for():
    balanced = reconcile.controls_for("transactions", 100, 100, 96, 4, Decimal("5000.10"), Decimal("5000.10"))
    assert [c.status for c in balanced] == ["PASS", "PASS", "PASS"]

    lost_row = reconcile.controls_for("transactions", 100, 100, 95, 4, Decimal("5000.10"), Decimal("4990.10"))
    assert [(c.check_name, c.status) for c in lost_row] == [
        ("rows_delivered_vs_staged", "PASS"), ("rows_staged_vs_loaded_plus_rejected", "FAIL"),
        ("amount_control_total", "FAIL")]

    truncated = reconcile.controls_for("customers", 100, 80, 80, 0, None, None)
    assert [c.status for c in truncated] == ["FAIL", "PASS"]
    assert "customers.rows_delivered_vs_staged" in str(reconcile.ReconciliationError([truncated[0]]))


def test_header_and_checksum_helpers():
    payload = b"a,b,\"c d\"\n1,2,3\n"
    assert extract.read_header(payload) == ["a", "b", "c d"]
    assert extract.sha256_of(payload) == hashlib.sha256(payload).hexdigest()


def test_money_is_formatted_exactly_from_minor_units():
    assert fmt_money(np.array([0, 7, 4999, 123456789, -250, -5])) == ["0.00", "0.07", "49.99", "1234567.89", "-2.50", "-0.05"]


def test_month_arithmetic_crosses_year_ends_in_both_directions():
    assert month_start(date(2023, 11, 1), 2) == date(2024, 1, 1)
    assert month_start(date(2023, 11, 1), 14) == date(2025, 1, 1)
    assert month_start(date(2023, 11, 1), -11) == date(2022, 12, 1)


def _digest(folder) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(folder).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_the_generator_is_deterministic_and_reports_what_it_injected(tmp_path):
    first = generate(GeneratorConfig(tmp_path / "a", months=3, scale=0.02, seed=7))
    generate(GeneratorConfig(tmp_path / "b", months=3, scale=0.02, seed=7))
    generate(GeneratorConfig(tmp_path / "c", months=3, scale=0.02, seed=8))
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")
    assert _digest(tmp_path / "a") != _digest(tmp_path / "c")

    assert first.batches == ["2023-11", "2023-12", "2024-01"]
    assert first.rows["transactions.csv"] > 500
    assert first.rows["fees.csv"] < first.rows["transactions.csv"]
    injected = {defect for (_batch, _file, defect) in first.defects}
    assert {"duplicate_transaction_id", "missing_customer"} <= injected
    for batch in first.batches:        # every batch is verifiable as delivered
        assert extract.verify_batch(tmp_path / "a" / batch, batch).keys() == extract.SOURCES.keys()
