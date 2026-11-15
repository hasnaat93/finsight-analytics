"""The pipeline against a real PostgreSQL: hand-built batches with known answers, then a
generated data set checked against the generator's own count of what it broke."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

from conftest import fee, fx_rows, merchant, needs_database, txn

from etl import load_facts, run_pipeline

pytestmark = needs_database

B1, B2, B3 = "2025-01", "2025-02", "2025-03"


# ------------------------------------------------------------ validation

def test_bad_rows_are_quarantined_under_the_rule_that_caught_them(lab):
    good = txn("T1", "2025-01-10T09:30:00")
    bad = {
        "missing_transaction_id": [txn("", "2025-01-10T10:00:00"), txn("  ", "2025-01-10T10:00:00")],
        "invalid_timestamp": [txn("T2", "2025-13-45T99:00:00"), txn("T3", "yesterday"), txn("T4", "")],
        "future_timestamp": [txn("T5", "2025-02-01T00:00:00"), txn("T6", "2027-06-15T12:00:00")],
        "non_positive_amount": [txn("T7", "2025-01-10T10:00:00", amount="-25.00"),
                                txn("T8", "2025-01-10T10:00:00", amount="0.00"),
                                txn("T9", "2025-01-10T10:00:00", amount="12.345"),
                                txn("T10", "2025-01-10T10:00:00", amount="ten")],
        "unknown_currency": [txn("T11", "2025-01-10T10:00:00", currency="XXX"),
                             txn("T12", "2025-01-10T10:00:00", currency="")],
        "invalid_status": [txn("T13", "2025-01-10T10:00:00", status="PENDING_REVIEW")],
        "duplicate_transaction_id": [list(good)],
    }
    rows = [good] + [row for group in bad.values() for row in group]
    result = lab.load(B1, merchants=[merchant()], customers=[["c1", "US", "Consumer"]], transactions=rows,
                      fees=[fee("T1")])

    assert lab.all("select transaction_id from dwh.fact_transaction") == [("T1",)]
    assert result.rejected["transactions"] == {rule: len(group) for rule, group in bad.items()}

    # the quarantined record is the source row as delivered, with the reason attached
    reason, record = lab.one("select reason, record from audit.rejected_rows where record->>'transaction_id' = 'T9'")
    assert "not greater than zero" in reason
    assert record["amount"] == "12.345" and record["merchant_id"] == "M1"

    # every delivered row is accounted for
    assert lab.all("""select check_name, source_value, target_value, status from audit.reconciliation
                      where entity = 'transactions' order by check_name""") == [
        ("amount_control_total", Decimal("100.00"), Decimal("100.00"), "PASS"),
        ("rows_delivered_vs_staged", Decimal(len(rows)), Decimal(len(rows)), "PASS"),
        ("rows_staged_vs_loaded_plus_rejected", Decimal(len(rows)), Decimal(len(rows)), "PASS"),
    ]


def test_a_duplicate_keeps_the_latest_version(lab):
    lab.load(B1, merchants=[merchant()], transactions=[
        txn("T1", "2025-01-10T09:00:00", amount="50.00", status="DECLINED", reason="DO_NOT_HONOR"),
        txn("T1", "2025-01-10T09:05:00", amount="50.00"),
    ], fees=[fee("T1")])
    assert lab.all("select transaction_id, is_captured from dwh.fact_transaction") == [("T1", True)]
    assert lab.rejected() == {("transactions", "duplicate_transaction_id"): 1}


def test_missing_merchant_or_customer_loads_against_the_unknown_member(lab):
    lab.load(B1, merchants=[merchant()], customers=[["c1", "US", "Consumer"]], transactions=[
        txn("T1", "2025-01-10T09:00:00", merchant="", status="DECLINED", reason="DO_NOT_HONOR"),
        txn("T2", "2025-01-10T09:00:00", merchant="M404", status="DECLINED", reason="DO_NOT_HONOR"),
        txn("T3", "2025-01-10T09:00:00", customer="", status="VOIDED"),
        txn("T4", "2025-01-10T09:00:00", customer="nobody", status="VOIDED"),
    ])
    m1 = lab.value("select merchant_key from dwh.dim_merchant where merchant_id = 'M1'")
    us = lab.value("select geography_key from dwh.dim_geography where country_code = 'US'")
    assert lab.all("select transaction_id, merchant_key, geography_key, customer_key > 0 "
                   "from dwh.fact_transaction order by 1") == [
        ("T1", -1, -1, True), ("T2", -1, -1, True), ("T3", m1, us, False), ("T4", m1, us, False)]
    assert lab.rejected() == {}


def test_refunds_chargebacks_and_fees_are_checked_against_the_payment(lab):
    lab.load(
        B1, merchants=[merchant()],
        transactions=[txn("T1", "2025-01-10T09:00:00", amount="100.00"),
                      txn("T2", "2025-01-11T09:00:00", amount="40.00", status="DECLINED", reason="EXPIRED_CARD")],
        fees=[fee("T1"), fee("T2"), fee("T1", "9.99")],
        refunds=[["R1", "T1", "2025-01-12T10:00:00", "30.00", "USD", "ITEM_RETURNED"],
                 ["R2", "T404", "2025-01-12T10:00:00", "5.00", "USD", "CUSTOMER_REQUEST"],
                 ["R3", "T2", "2025-01-12T10:00:00", "5.00", "USD", "CUSTOMER_REQUEST"],
                 ["R4", "T1", "2025-01-12T10:00:00", "100.01", "USD", "CUSTOMER_REQUEST"],
                 ["R1", "T1", "2025-01-12T10:00:00", "30.00", "USD", "ITEM_RETURNED"],
                 ["R5", "T1", "2025-01-13T10:00:00", "100.00", "USD", "CANCELLED_ORDER"]],
        chargebacks=[["C1", "T1", "2025-01-20", "100.00", "USD", "FRAUD", "LOST"],
                     ["C2", "T404", "2025-01-20", "10.00", "USD", "FRAUD", "WON"],
                     ["C3", "T1", "2025-01-20", "10.00", "USD", "FRAUD", "MAYBE"]],
    )
    assert lab.rejected() == {
        ("fees", "fee_without_capture"): 1, ("fees", "duplicate_fee"): 1,
        ("refunds", "orphan_refund"): 2, ("refunds", "refund_exceeds_original"): 1, ("refunds", "duplicate_refund_id"): 1,
        ("chargebacks", "orphan_chargeback"): 1, ("chargebacks", "invalid_outcome"): 1,
    }
    assert lab.all("select refund_id, refund_original, is_partial from dwh.fact_refund order by 1") == [
        ("R1", Decimal("30.00"), True), ("R5", Decimal("100.00"), False)]
    assert lab.all("select chargeback_id, outcome from dwh.fact_chargeback") == [("C1", "LOST")]
    assert lab.value("select count(*) from dwh.fact_fee") == 1


# ------------------------------------------------------------- conformance

def test_amounts_are_converted_at_the_latest_rate_on_or_before_the_day(lab):
    rates = fx_rows(B1, eur="1.10") + [["2025-01-15", "EUR", "1.20"]]
    lab.load(B1, merchants=[merchant(), merchant("M2", "Beta GmbH", country="DE")], fx_rates=rates, transactions=[
        txn("T1", "2025-01-10T09:00:00", merchant="M2", amount="100.00", currency="EUR"),
        txn("T2", "2025-01-15T09:00:00", merchant="M2", amount="100.00", currency="EUR"),
        txn("T3", "2025-01-28T09:00:00", merchant="M2", amount="33.33", currency="EUR"),
        txn("T4", "2025-01-28T09:00:00", amount="100.00"),
    ], fees=[fee("T1", "3.00", "1.00", "0.10", "EUR"), fee("T2"), fee("T3"), fee("T4")])
    assert lab.all("select transaction_id, amount_original, amount_reporting from dwh.fact_transaction order by 1") == [
        ("T1", Decimal("100.00"), Decimal("110.00")), ("T2", Decimal("100.00"), Decimal("120.00")),
        ("T3", Decimal("33.33"), Decimal("40.00")), ("T4", Decimal("100.00"), Decimal("100.00"))]
    # a fee converts at its payment's rate, so the take rate is the same in either currency
    assert lab.one("""select fee_original, fee_amount, interchange_cost from dwh.fact_fee e
                      join dwh.fact_transaction f using (transaction_key) where f.transaction_id = 'T1'""") == (
        Decimal("3.00"), Decimal("3.30"), Decimal("1.10"))
    assert lab.one("select g.country_name, c.currency_code from dwh.fact_transaction f "
                   "join dwh.dim_geography g using (geography_key) join dwh.dim_currency c using (currency_key) "
                   "where transaction_id = 'T1'") == ("Germany", "EUR")


def test_labels_are_standardised_and_new_dimension_members_are_created(lab):
    lab.load(B1, merchants=[merchant()], transactions=[
        txn("T1", "2025-01-10T14:45:00", method="WALLET", brand="APPLE_PAY", card_type="", channel="MOBILE"),
        txn("T2", "2025-01-10T23:59:59", method="BANK_TRANSFER", brand="", card_type="", channel="RECURRING",
            status="DECLINED", reason="INSUFFICIENT_FUNDS"),
    ], fees=[fee("T1")])
    assert lab.all("""select f.transaction_id, f.hour_of_day, p.payment_method, p.card_brand, p.card_type,
                             c.channel_name, s.status, s.decline_reason, s.is_success
                      from dwh.fact_transaction f
                      join dwh.dim_payment_method p using (method_key) join dwh.dim_channel c using (channel_key)
                      join dwh.dim_transaction_status s using (status_key) order by 1""") == [
        ("T1", 14, "Wallet", "Apple Pay", "Not Applicable", "Mobile app", "Captured", "Not declined", True),
        ("T2", 23, "Bank Transfer", "Not Applicable", "Not Applicable", "Recurring", "Declined", "Insufficient Funds", False),
    ]


# ---------------------------------------------- slowly changing dimension

def test_a_merchant_change_opens_a_new_version_and_payments_keep_their_own(lab):
    lab.load(B1, merchants=[merchant(plan="STANDARD")], transactions=[txn("T1", "2025-01-10T09:00:00")], fees=[fee("T1")])
    lab.load(B2, merchants=[merchant(plan="STANDARD")], transactions=[txn("T2", "2025-02-10T09:00:00")], fees=[fee("T2")])
    lab.load(B3, merchants=[merchant(name="Alpha Stores Ltd", plan="ENTERPRISE", risk="HIGH")],
             transactions=[txn("T3", "2025-03-10T09:00:00")], fees=[fee("T3")])

    assert lab.all("""select merchant_name, pricing_plan, risk_tier, valid_from::text, valid_to::text, is_current
                      from dwh.dim_merchant where merchant_id = 'M1' order by valid_from""") == [
        ("Alpha Stores Ltd", "Standard", "Low", "1900-01-01", "2025-03-01", False),     # the rename is type 1
        ("Alpha Stores Ltd", "Enterprise", "High", "2025-03-01", "9999-12-31", True),
    ]
    assert lab.all("""select f.transaction_id, m.pricing_plan from dwh.fact_transaction f
                      join dwh.dim_merchant m using (merchant_key) order by 1""") == [
        ("T1", "Standard"), ("T2", "Standard"), ("T3", "Enterprise")]


def test_reloading_an_older_batch_does_not_rewrite_merchant_history(lab):
    lab.load(B1, merchants=[merchant(plan="STANDARD")], transactions=[txn("T1", "2025-01-10T09:00:00")], fees=[fee("T1")])
    lab.load(B2, merchants=[merchant(plan="PLUS")])
    versions = lab.all("select pricing_plan, valid_from, is_current from dwh.dim_merchant where merchant_id = 'M1' order by 2")

    result = lab.run(B1, reload=True)
    assert result.status == "SUCCESS" and result.merchant_history_applied is False
    assert lab.all("select pricing_plan, valid_from, is_current from dwh.dim_merchant where merchant_id = 'M1' order by 2") == versions


# ------------------------------------------------------------ settlements

def test_a_settlement_is_reconciled_against_what_the_warehouse_expects(lab):
    lab.load(
        B1, merchants=[merchant()],
        transactions=[txn("T1", "2025-01-10T09:00:00", amount="100.00"), txn("T2", "2025-01-10T18:00:00", amount="50.00"),
                      txn("T3", "2025-01-10T19:00:00", amount="70.00", status="DECLINED", reason="DO_NOT_HONOR"),
                      txn("T4", "2025-01-11T09:00:00", amount="200.00")],
        fees=[fee("T1", "3.20"), fee("T2", "1.75"), fee("T4", "6.10")],
        refunds=[["R1", "T1", "2025-01-11T10:00:00", "30.00", "USD", "ITEM_RETURNED"]],
        chargebacks=[["C1", "T2", "2025-01-11", "50.00", "USD", "FRAUD", "PENDING"]],
        settlements=[
            # day 10: 150.00 captured - 4.95 fees = 145.05
            ["S1", "M1", "2025-01-11", "USD", "150.00", "4.95", "0.00", "0.00", "145.05"],
            # day 11: 200.00 - 6.10 fees - 30.00 refund - 50.00 chargeback = 113.90, but 110.00 was paid
            ["S2", "M1", "2025-01-12", "USD", "200.00", "6.10", "30.00", "50.00", "110.00"],
        ],
    )
    assert lab.all("""select settlement_batch_id, expected_payout_original, net_payout_original, variance, is_reconciled
                      from dwh.fact_settlement order by 1""") == [
        ("S1", Decimal("145.05"), Decimal("145.05"), Decimal("0.00"), True),
        ("S2", Decimal("113.90"), Decimal("110.00"), Decimal("-3.90"), False),
    ]
    # an underpayment is a finding to report, not a row to reject
    assert lab.rejected() == {}


# ------------------------------------------------- incremental, idempotent

def test_only_new_batches_are_loaded_and_a_reload_changes_nothing(lab):
    for batch, day in ((B1, "2025-01-10"), (B2, "2025-02-10")):
        lab.deliver(batch, merchants=[merchant()], transactions=[txn(f"T-{batch}", f"{day}T09:00:00"),
                                                                 txn(f"X-{batch}", f"{day}T09:00:00", amount="-1")],
                    fees=[fee(f"T-{batch}")])
    assert [r.status for r in run_pipeline.run_pending(lab.settings)] == ["SUCCESS", "SUCCESS"]
    assert run_pipeline.run_pending(lab.settings) == []                     # nothing pending
    assert lab.run(B1).status == "SKIPPED"

    snapshot = """select (select count(*) from dwh.fact_transaction), (select count(*) from dwh.fact_fee),
                         (select count(*) from dwh.dim_merchant), (select count(*) from audit.rejected_rows),
                         (select count(*) from audit.etl_run where status = 'SUCCESS'),
                         (select count(*) from audit.reconciliation where status = 'FAIL')"""
    before = lab.one(snapshot)
    assert before == (2, 2, 2, 2, 2, 0)
    assert lab.run(B1, reload=True).status == "SUCCESS"
    assert lab.run(B2, reload=True).status == "SUCCESS"
    assert lab.one(snapshot) == before

    lab.deliver(B3, merchants=[merchant()], transactions=[txn("T-new", "2025-03-05T09:00:00")], fees=[fee("T-new")])
    assert [r.batch_id for r in run_pipeline.run_pending(lab.settings)] == [B3]
    assert lab.value("select count(*) from dwh.fact_transaction") == 3


# -------------------------------------------------------- failure handling

def _state(lab):
    return lab.one("""select (select count(*) from dwh.fact_transaction), (select count(*) from dwh.dim_merchant),
                             (select count(*) from dwh.fx_rate), (select count(*) from audit.rejected_rows)""")


def test_a_tampered_file_fails_the_batch_and_loads_nothing(lab):
    lab.load(B1, merchants=[merchant()], transactions=[txn("T1", "2025-01-10T09:00:00")], fees=[fee("T1")])
    before = _state(lab)
    folder = lab.deliver(B2, merchants=[merchant(plan="PLUS")], transactions=[txn("T2", "2025-02-10T09:00:00")],
                         fees=[fee("T2")])
    with (folder / "transactions.csv").open("a", encoding="utf-8", newline="") as handle:
        handle.write(",".join(txn("T-EVIL", "2025-02-11T09:00:00", amount="999999.00")) + "\n")

    result = lab.run(B2)
    assert result.status == "FAILED" and "checksum does not match" in result.error
    assert _state(lab) == before
    assert lab.one(f"select status, position('checksum' in error) > 0 from audit.etl_run where batch_id = '{B2}'") == (
        "FAILED", True)


def test_a_changed_layout_or_a_missing_file_fails_the_batch(lab):
    folder = lab.deliver(B1, merchants=[merchant()])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    path = folder / "refunds.csv"
    path.write_bytes(path.read_bytes().replace(b"refund_id", b"id"))
    import hashlib
    manifest["files"]["refunds.csv"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = lab.run(B1)
    assert result.status == "FAILED" and "unexpected columns" in result.error

    lab.deliver(B2, merchants=[merchant()])
    (lab.settings.raw_dir / B2 / "fees.csv").unlink()
    assert "fees.csv is missing" in lab.run(B2).error
    assert "No manifest" in lab.run(B3).error


def test_a_failed_control_rolls_the_batch_back_but_keeps_the_evidence(lab, monkeypatch):
    lab.load(B1, merchants=[merchant()], transactions=[txn("T1", "2025-01-10T09:00:00")], fees=[fee("T1")])
    before = _state(lab)
    lab.deliver(B2, merchants=[merchant(plan="PLUS")], fees=[fee("T2"), fee("T3")],
                transactions=[txn("T2", "2025-02-10T09:00:00"), txn("T3", "2025-02-11T09:00:00", amount="40.00")])

    real_load = load_facts.load_transactions

    def lossy_load(conn, batch_id):       # a defect in a load step: one payment silently dropped
        real_load(conn, batch_id)
        conn.execute("delete from dwh.fact_transaction where transaction_id = 'T3'")

    monkeypatch.setattr(load_facts, "load_transactions", lossy_load)
    result = lab.run(B2)
    assert result.status == "FAILED" and "Reconciliation failed" in result.error
    assert _state(lab) == before                                    # no payment, no new merchant version
    failed = lab.all("select entity, check_name from audit.reconciliation where status = 'FAIL' order by 1, 2")
    assert ("transactions", "rows_staged_vs_loaded_plus_rejected") in failed
    assert ("transactions", "amount_control_total") in failed

    monkeypatch.undo()
    assert lab.run(B2).status == "SUCCESS"                          # the same delivery loads once the defect is gone
    assert lab.value("select count(*) from dwh.fact_transaction") == 3


# ------------------------------------------------- generated data, end to end

def test_every_generated_batch_loads_and_reconciles(generated):
    assert [r.status for r in generated.results] == ["SUCCESS"] * 12
    assert generated.value("select count(*) from audit.reconciliation where status = 'FAIL'") == 0
    assert generated.value("select count(*) from dwh.fact_transaction") > 8000


def test_the_quarantine_holds_exactly_what_the_generator_broke(generated):
    """The generator counts every defect it injects; the pipeline must catch each one, and nothing else."""
    not_rejected = {"missing_customer", "missing_merchant", "settlement_mismatch"}
    expected = Counter()
    for (_batch, file_name, defect), count in generated.generation.defects.items():
        if defect not in not_rejected:
            expected[(file_name.removesuffix(".csv"), defect)] += count
    assert generated.rejected() == dict(expected)
    assert sum(expected.values()) > 50


def test_defects_that_are_not_rejections_are_still_visible(generated):
    injected = Counter()
    for (_batch, _file, defect), count in generated.generation.defects.items():
        injected[defect] += count
    assert generated.value("select count(*) from dwh.fact_transaction where merchant_key = -1") == injected["missing_merchant"]
    assert generated.value("select count(*) from dwh.fact_transaction where customer_key = -1") == injected["missing_customer"]
    # every settlement the generator altered is reported as unreconciled, and no other
    assert generated.value("select count(*) from dwh.fact_settlement where not is_reconciled") == injected["settlement_mismatch"]
    assert injected["settlement_mismatch"] > 5


def test_all_sql_assertions_pass_on_the_loaded_warehouse(generated):
    outcomes = run_pipeline.run_sql_tests(generated.conn)
    assert len(outcomes) == 15
    assert [name for name, violations in outcomes if violations] == []


def test_mart_views_expose_the_star_schema_for_power_bi(generated):
    views = {r[0] for r in generated.all("select table_name from information_schema.views where table_schema = 'mart'")}
    assert {"dim_date", "dim_merchant", "dim_customer", "dim_payment_method", "dim_channel", "dim_geography",
            "dim_currency", "dim_transaction_status", "fact_transaction", "fact_refund", "fact_chargeback",
            "fact_fee", "fact_settlement", "etl_rejections", "etl_batches", "reconciliation_controls",
            "region_access"} <= views
    assert generated.value("select count(*) from mart.fact_transaction") == generated.value("select count(*) from dwh.fact_transaction")
    first, last = generated.one("select min(date), max(date) from mart.dim_date")
    assert str(first) == "2023-11-01" and str(last) == "2024-12-31"
    assert generated.value("select count(*) from mart.etl_batches where controls_run = controls_passed") == 12


def test_analysis_queries_run(generated):
    from etl.db import SQL_DIR
    for path in sorted((SQL_DIR / "analysis").glob("*.sql")):
        rows = generated.conn.execute(path.read_text(encoding="utf-8")).fetchall()
        assert rows is not None, path.name
    growth = generated.conn.execute((SQL_DIR / "analysis" / "01_merchant_month_over_month_growth.sql").read_text()).fetchall()
    assert len(growth) > 100
