"""Synthetic source data for FinSight Analytics.

Simulates what a payment processor's operational systems would export: one folder per
month containing merchants, customers, transactions, refunds, chargebacks, fees,
settlements and FX rates, plus a manifest of row counts and checksums.

Everything is fictional and reproducible: the same seed and scale give byte-identical
files. Amounts are handled as integer minor units and only formatted when written.

The data is shaped to give the reports something to find:

* weekly and yearly seasonality, with a Black Friday and December peak for retail
* merchants that grow, decline, churn or join part-way through
* pricing plan and risk tier changes over time (exercises the type 2 dimension)
* four settlement currencies converted with daily FX rates

and it is deliberately dirty. Defects are injected at configurable rates: duplicates,
non-positive amounts, unknown currencies, future and malformed timestamps, missing
merchant and customer ids, orphan refunds and chargebacks, refunds larger than the
payment, fees for payments that were never captured, and settlement batches whose
payout does not add up. ``generate`` returns the exact number of each defect it
injected, which the test suite compares with what the pipeline quarantined.

Usage:
    python -m data_generator.generate_data --scale 1.0 --out data/raw
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from faker import Faker

# ------------------------------------------------------------------ reference data

COUNTRIES = [  # code, weight, settlement currency
    ("US", 30, "USD"), ("GB", 13, "GBP"), ("DE", 10, "EUR"), ("FR", 8, "EUR"), ("NL", 5, "EUR"),
    ("ES", 5, "EUR"), ("IT", 5, "EUR"), ("IE", 2, "EUR"), ("PK", 9, "PKR"), ("CA", 5, "USD"),
    ("AE", 3, "USD"), ("SG", 2, "USD"), ("AU", 3, "USD"),
]
CURRENCIES = ["USD", "EUR", "GBP", "PKR"]
BASE_RATE_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "PKR": 0.0036}

# name, mcc, weight, median ticket (USD), ticket sigma, channel mix (ECOM, POS, MOBILE, RECURRING),
# weekend factor, seasonality profile, refund rate
CATEGORIES = [
    ("Retail", "5311", 22, 48.0, 0.85, (0.45, 0.35, 0.18, 0.02), 1.20, "holiday", 0.050),
    ("Grocery", "5411", 12, 34.0, 0.60, (0.10, 0.75, 0.14, 0.01), 1.25, "flat", 0.012),
    ("Restaurants", "5812", 13, 27.0, 0.55, (0.08, 0.66, 0.25, 0.01), 1.45, "flat", 0.010),
    ("Travel", "4722", 7, 310.0, 0.95, (0.70, 0.08, 0.21, 0.01), 0.95, "summer", 0.045),
    ("Digital Services", "5817", 12, 14.0, 0.50, (0.25, 0.00, 0.30, 0.45), 1.00, "flat", 0.020),
    ("Electronics", "5732", 8, 190.0, 0.90, (0.60, 0.25, 0.14, 0.01), 1.15, "holiday", 0.060),
    ("Health & Beauty", "5977", 9, 42.0, 0.65, (0.40, 0.40, 0.18, 0.02), 1.10, "mild", 0.030),
    ("Education", "8299", 5, 120.0, 0.80, (0.55, 0.05, 0.10, 0.30), 0.60, "term", 0.015),
    ("Utilities", "4900", 6, 85.0, 0.45, (0.20, 0.02, 0.18, 0.60), 0.70, "flat", 0.004),
    ("Entertainment", "7832", 6, 31.0, 0.60, (0.40, 0.30, 0.28, 0.02), 1.60, "mild", 0.025),
]
CHANNELS = ["ECOMMERCE", "POS", "MOBILE", "RECURRING"]
PRICING = {"STANDARD": (290, 30), "PLUS": (250, 25), "ENTERPRISE": (190, 10)}   # bps, fixed (USD cents)
RISK_TIERS = ["LOW", "MEDIUM", "HIGH"]
DECLINE_REASONS = ["INSUFFICIENT_FUNDS", "DO_NOT_HONOR", "EXPIRED_CARD", "SUSPECTED_FRAUD",
                   "INVALID_CARD", "LIMIT_EXCEEDED", "AUTHENTICATION_FAILED"]
DECLINE_WEIGHTS = [0.36, 0.20, 0.10, 0.11, 0.07, 0.08, 0.08]
REFUND_REASONS = ["CUSTOMER_REQUEST", "ITEM_RETURNED", "DUPLICATE_CHARGE", "ITEM_NOT_RECEIVED", "CANCELLED_ORDER"]
CHARGEBACK_REASONS = ["FRAUD", "PRODUCT_NOT_RECEIVED", "NOT_AS_DESCRIBED", "DUPLICATE_PROCESSING", "CREDIT_NOT_PROCESSED"]

# hour-of-day shape per channel (24 relative weights)
_DAYTIME = [1, 1, 1, 1, 1, 2, 4, 7, 10, 12, 13, 14, 15, 14, 13, 12, 12, 13, 13, 11, 8, 5, 3, 2]
_EVENING = [3, 2, 1, 1, 1, 1, 2, 4, 6, 8, 9, 10, 11, 10, 10, 10, 11, 13, 15, 17, 17, 14, 9, 5]
_FLAT = [4] * 24
_NIGHT_BATCH = [9, 12, 12, 9, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 6]
HOUR_SHAPES = [_EVENING, _DAYTIME, _EVENING, _NIGHT_BATCH]   # ECOMMERCE, POS, MOBILE, RECURRING

FILES = {
    "merchants.csv": ["merchant_id", "merchant_name", "mcc", "category", "country", "onboarding_date",
                      "risk_tier", "pricing_plan", "status"],
    "customers.csv": ["customer_id", "country", "segment"],
    "transactions.csv": ["transaction_id", "merchant_id", "customer_id", "transaction_ts", "amount", "currency",
                         "payment_method", "card_brand", "card_type", "channel", "status", "decline_reason"],
    "refunds.csv": ["refund_id", "transaction_id", "refund_ts", "amount", "currency", "reason"],
    "chargebacks.csv": ["chargeback_id", "transaction_id", "chargeback_date", "amount", "currency",
                        "reason_code", "outcome"],
    "fees.csv": ["transaction_id", "merchant_fee", "interchange_cost", "scheme_cost", "currency"],
    "settlements.csv": ["settlement_batch_id", "merchant_id", "settlement_date", "currency", "gross_amount",
                        "fee_amount", "refund_amount", "chargeback_amount", "net_payout"],
    "fx_rates.csv": ["rate_date", "currency", "rate_to_usd"],
}


@dataclass
class DefectRates:
    """Share of rows (relative to the clean rows of the same file) carrying each defect."""

    duplicate_transaction: float = 0.0015
    non_positive_amount: float = 0.0004
    unknown_currency: float = 0.0003
    future_timestamp: float = 0.0002
    malformed_timestamp: float = 0.0001
    invalid_status: float = 0.0001
    missing_merchant: float = 0.0005        # not rejected: mapped to the Unknown merchant
    missing_customer: float = 0.002         # not rejected: mapped to the Unknown customer
    orphan_refund: float = 0.005
    refund_exceeds_original: float = 0.003
    duplicate_refund: float = 0.002
    orphan_chargeback: float = 0.005
    fee_without_capture: float = 0.0002
    settlement_mismatch: float = 0.004      # not rejected: loaded and reported as unreconciled
    duplicate_settlement: float = 0.001


@dataclass
class GeneratorConfig:
    out_dir: Path
    start: date = date(2023, 11, 1)
    months: int = 36
    scale: float = 1.0                      # 1.0 is about 1.5 million transactions and 500 merchants
    seed: int = 20231101
    defects: DefectRates = field(default_factory=DefectRates)


@dataclass
class GenerationResult:
    batches: list[str]
    rows: Counter                           # clean + defective rows written, per file
    defects: Counter                        # defects injected, per (batch, file, defect)
    out_dir: Path

    def defects_for(self, batch: str) -> dict[tuple[str, str], int]:
        return {(f, d): n for (b, f, d), n in self.defects.items() if b == batch}


def month_start(start: date, offset: int) -> date:
    index = start.year * 12 + (start.month - 1) + offset
    return date(index // 12, index % 12 + 1, 1)


def fmt_money(cents: np.ndarray) -> list[str]:
    """Integer minor units to decimal text, exactly."""
    values = cents.tolist()
    return [f"-{-c // 100}.{-c % 100:02d}" if c < 0 else f"{c // 100}.{c % 100:02d}" for c in values]


def _choice_rows(rng: np.random.Generator, cumulative: np.ndarray) -> np.ndarray:
    """Per-row categorical draw; cumulative has one row of cumulative probabilities per draw."""
    return (rng.random(len(cumulative))[:, None] > cumulative).sum(axis=1)


def _seasonality(profile: str, day: date) -> float:
    doy = day.timetuple().tm_yday
    month = day.month
    if profile == "holiday":
        factor = 1.0
        if month == 11:
            factor = 1.15
            # Black Friday: the day after the fourth Thursday of November, and the following weekend
            first = date(day.year, 11, 1)
            fourth_thursday = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
            if 1 <= (day - fourth_thursday).days <= 4:
                factor = 2.4
        elif month == 12:
            factor = 1.55 if day.day <= 24 else 1.05
        elif month == 1:
            factor = 0.82
        return factor
    if profile == "summer":
        return 1.0 + 0.45 * np.exp(-((doy - 196) / 38.0) ** 2) + (0.15 if month == 12 else 0.0)
    if profile == "term":
        return 1.6 if month in (1, 8, 9) else 0.85
    if profile == "mild":
        return 1.18 if month == 12 else 1.0
    return 1.0


class Generator:
    def __init__(self, config: GeneratorConfig) -> None:
        self.cfg = config
        self.rng = np.random.default_rng(config.seed)
        self.first_day = config.start
        self.last_day = month_start(config.start, config.months) - timedelta(days=1)
        self.total_days = (self.last_day - self.first_day).days + 1
        self.result = GenerationResult([], Counter(), Counter(), Path(config.out_dir))
        self._build_fx()
        self._build_merchants()
        self._build_customers()
        # Per merchant and day, in minor units of the merchant's currency: what a settlement pays on.
        shape = (self.n_merchants, self.total_days + 1)
        self.day_gross = np.zeros(shape, dtype=np.int64)
        self.day_fees = np.zeros(shape, dtype=np.int64)
        self.day_refunds = np.zeros(shape, dtype=np.int64)
        self.day_chargebacks = np.zeros(shape, dtype=np.int64)
        self.pending_refunds: dict[int, list[tuple]] = {}        # month offset -> rows
        self.pending_chargebacks: dict[int, list[tuple]] = {}
        self.txn_seq = 0
        self.refund_seq = 0
        self.chargeback_seq = 0
        self.settlement_seq = 0
        self.junk_seq = 0

    # ----------------------------------------------------------------- set-up

    def _build_fx(self) -> None:
        """A slow random walk per currency around its base rate."""
        self.fx = {"USD": np.ones(self.total_days + 1)}
        for currency in CURRENCIES[1:]:
            steps = self.rng.normal(0, 0.0035, self.total_days + 1).cumsum()
            self.fx[currency] = BASE_RATE_TO_USD[currency] * np.exp(steps - steps.mean())

    def _build_merchants(self) -> None:
        cfg, rng = self.cfg, self.rng
        n = max(24, round(500 * cfg.scale))
        self.n_merchants = n
        fake = Faker("en_US")
        fake.seed_instance(cfg.seed)
        names: list[str] = []
        seen: set[str] = set()
        while len(names) < n:
            name = fake.company()
            if name not in seen:
                seen.add(name)
                names.append(name)
        self.m_name = names
        self.m_id = [f"M{100000 + i}" for i in range(n)]

        weights = np.array([c[1] for c in COUNTRIES], dtype=float)
        self.m_country_idx = rng.choice(len(COUNTRIES), size=n, p=weights / weights.sum())
        self.m_currency = [COUNTRIES[i][2] for i in self.m_country_idx]
        self.m_rate = np.array([BASE_RATE_TO_USD[c] for c in self.m_currency])

        cat_weights = np.array([c[2] for c in CATEGORIES], dtype=float)
        self.m_cat = rng.choice(len(CATEGORIES), size=n, p=cat_weights / cat_weights.sum())

        # Heavy-tailed size: a few merchants carry a large share of the volume.
        self.m_size = rng.lognormal(0.0, 1.25, n)

        # 68% trade from the first day; the rest join over the first 33 months.
        joined_before = rng.random(n) < 0.68
        self.m_join = np.where(joined_before, -rng.integers(1, 48, n), rng.integers(1, max(2, cfg.months - 3), n))

        kind = rng.choice(4, size=n, p=[0.55, 0.20, 0.15, 0.10])     # stable, growing, declining, churning
        annual = np.select(
            [kind == 0, kind == 1, kind == 2, kind == 3],
            [rng.uniform(-0.06, 0.10, n), rng.uniform(0.30, 0.85, n), rng.uniform(-0.55, -0.28, n),
             rng.uniform(-0.10, 0.10, n)])
        self.m_monthly_growth = (1.0 + annual) ** (1.0 / 12.0)
        self.m_churn = np.where(kind == 3, rng.integers(8, max(9, cfg.months), n), 10_000)

        self.m_risk = rng.choice(3, size=n, p=[0.62, 0.28, 0.10])
        plans = list(PRICING)
        self.m_plan = rng.choice(3, size=n, p=[0.58, 0.30, 0.12])
        # Attribute history: month offset -> list of (merchant, attribute, new value)
        self.changes: dict[int, list[tuple[int, str, int]]] = {}
        for merchant in np.flatnonzero(rng.random(n) < 0.20):
            month = int(rng.integers(2, max(3, cfg.months - 1)))
            if rng.random() < 0.55:
                options = [p for p in range(3) if p != self.m_plan[merchant]]
                self.changes.setdefault(month, []).append((int(merchant), "plan", int(rng.choice(options))))
            else:
                options = [r for r in range(3) if r != self.m_risk[merchant]]
                self.changes.setdefault(month, []).append((int(merchant), "risk", int(rng.choice(options))))
        self._plans = plans

    def _build_customers(self) -> None:
        n = max(400, round(50_000 * self.cfg.scale))
        self.n_customers = n
        salt = str(self.cfg.seed).encode()
        self.c_id = [hashlib.sha1(salt + str(i).encode()).hexdigest()[:16] for i in range(n)]
        weights = np.array([c[1] for c in COUNTRIES], dtype=float)
        self.c_country_idx = self.rng.choice(len(COUNTRIES), size=n, p=weights / weights.sum())
        self.c_segment = self.rng.choice(["Consumer", "Premium", "Business"], size=n, p=[0.80, 0.12, 0.08])

    def _customer_pool(self, month: int) -> int:
        """How many customers exist by the end of a month: the base grows from 40% to 100%."""
        share = 0.40 + 0.60 * (month + 1) / self.cfg.months
        return max(50, int(self.n_customers * min(1.0, share)))

    # ------------------------------------------------------------ one month

    def _month(self, month: int) -> dict[str, list[list[str]]]:
        cfg = self.cfg
        batch = month_start(cfg.start, month)
        batch_id = batch.strftime("%Y-%m")
        next_batch = month_start(cfg.start, month + 1)
        days = [(batch + timedelta(days=i)) for i in range((next_batch - batch).days)]
        day_offset = np.array([(d - self.first_day).days for d in days])

        for merchant, attribute, value in self.changes.get(month, []):
            if attribute == "plan":
                self.m_plan[merchant] = value
            else:
                self.m_risk[merchant] = value

        clean = self._transactions(month, days, day_offset)
        defects: Counter = Counter()
        content = {
            "merchants.csv": self._merchant_rows(month, defects),
            "customers.csv": self._customer_rows(month),
            "transactions.csv": self._transaction_rows(clean, days, defects),
            "refunds.csv": self._refund_rows(month, clean, defects),
            "chargebacks.csv": self._chargeback_rows(month, defects),
            "fees.csv": self._fee_rows(clean, defects),
            "settlements.csv": self._settlement_rows(days, day_offset, defects),
            "fx_rates.csv": [[d.isoformat(), c, f"{self.fx[c][o]:.6f}"]
                             for d, o in zip(days, day_offset, strict=True) for c in CURRENCIES],
        }
        for (file_name, defect), count in defects.items():
            self.result.defects[(batch_id, file_name, defect)] += count
        return content

    def _transactions(self, month: int, days: list[date], day_offset: np.ndarray) -> dict[str, np.ndarray]:
        """The month's real payments, as parallel arrays. Also schedules refunds and chargebacks."""
        cfg, rng = self.cfg, self.rng
        n_m, n_d = self.n_merchants, len(days)

        active = (self.m_join <= month) & (self.m_churn > month)
        age = np.clip(month - np.maximum(self.m_join, 0), 0, None)
        ramp = np.where(self.m_join > 0, np.minimum(1.0, 0.35 + 0.22 * age), 1.0)       # new merchants ramp up
        level = self.m_size * self.m_monthly_growth ** month * ramp * active

        weekend = np.array([d.weekday() >= 5 for d in days])
        season = np.array([[_seasonality(c[7], d) for d in days] for c in CATEGORIES])     # category x day
        weekly = np.where(weekend[None, :], np.array([c[6] for c in CATEGORIES])[:, None], 1.0)
        shape = (season * weekly)[self.m_cat]                                             # merchant x day

        # 0.52 offsets the merchants that join or grow after the first day, so that a scale of 1.0
        # lands near 1.5 million payments over the default 36 months.
        target_per_day = 0.52 * 1_500_000 * cfg.scale / self.total_days
        base = target_per_day / max(self.m_size[self.m_join <= 0].sum(), 1e-9)
        counts = rng.poisson(level[:, None] * shape * base)
        total = int(counts.sum())

        merchant = np.repeat(np.arange(n_m), counts.sum(axis=1))
        day = np.repeat(np.tile(np.arange(n_d), n_m), counts.ravel())
        cat = self.m_cat[merchant]

        channel = _choice_rows(rng, np.cumsum(np.array([c[5] for c in CATEGORIES]), axis=1)[cat])
        hour_cum = np.cumsum(np.array(HOUR_SHAPES, dtype=float) / np.sum(HOUR_SHAPES, axis=1, keepdims=True), axis=1)
        hour = np.minimum(_choice_rows(rng, hour_cum[channel]), 23)
        seconds = day_offset[day].astype(np.int64) * 86400 + hour * 3600 + rng.integers(0, 3600, total)

        # payment method: 0 card, 1 wallet, 2 bank transfer
        method_p = np.where((channel == 3)[:, None], [0.82, 0.03, 0.15], [0.78, 0.17, 0.05])
        method = _choice_rows(rng, np.cumsum(method_p, axis=1))
        brand = np.where(method == 0, rng.choice(4, total, p=[0.52, 0.34, 0.09, 0.05]),      # VISA MC AMEX DISCOVER
                         np.where(method == 1, 4 + rng.choice(3, total, p=[0.45, 0.30, 0.25]), 7))
        card_type = np.where(method == 0, rng.choice(3, total, p=[0.55, 0.40, 0.05]), 3)     # CREDIT DEBIT PREPAID

        median = np.array([c[3] for c in CATEGORIES])[cat]
        sigma = np.array([c[4] for c in CATEGORIES])[cat]
        usd = median * np.exp(rng.normal(0, 1, total) * sigma)
        amount = np.maximum(50, np.round(usd / self.m_rate[merchant] * 100)).astype(np.int64)

        decline_p = (0.055 + np.select([method == 2, method == 1], [0.020, -0.012], 0.0)
                     + np.where(brand == 2, 0.018, 0.0) + np.where(card_type == 2, 0.050, 0.0)
                     + np.array([0.0, 0.015, 0.055])[self.m_risk[merchant]]
                     + np.where(channel == 3, 0.030, 0.0) + np.where(usd > median * 4, 0.030, 0.0))
        roll = rng.random(total)
        declined = roll < decline_p
        voided = ~declined & (rng.random(total) < 0.012)
        status = np.where(declined, 1, np.where(voided, 2, 0))                               # CAPTURED DECLINED VOIDED
        reason = np.where(declined, rng.choice(len(DECLINE_REASONS), total, p=DECLINE_WEIGHTS), -1)

        pool = self._customer_pool(month)
        customer = np.minimum((pool * rng.random(total) ** 1.4).astype(np.int64), pool - 1)

        txn_no = self.txn_seq + np.arange(total)
        self.txn_seq += total

        captured = status == 0
        plan = self.m_plan[merchant]
        bps = np.array([PRICING[p][0] for p in self._plans])[plan]
        fixed_usd = np.array([PRICING[p][1] for p in self._plans])[plan]
        fee = np.where(method == 2, np.minimum((amount * 80 + 5000) // 10000, np.round(500 / self.m_rate[merchant])),
                       (amount * bps + 5000) // 10000 + np.round(fixed_usd / self.m_rate[merchant])).astype(np.int64)
        fee = np.minimum(fee, amount)
        interchange_bps = np.select(
            [method == 2, method == 1, brand == 2, card_type == 0, card_type == 1], [20, 130, 190, 150, 60], 90)
        interchange = (amount * interchange_bps + 5000) // 10000
        scheme = (amount * np.where(method == 0, 12, 5) + 5000) // 10000

        day_abs = day_offset[day]
        np.add.at(self.day_gross, (merchant[captured], day_abs[captured]), amount[captured])
        np.add.at(self.day_fees, (merchant[captured], day_abs[captured]), fee[captured])

        self._schedule_refunds(month, captured, merchant, cat, seconds, amount, txn_no)
        self._schedule_chargebacks(month, captured, merchant, method, seconds, amount, txn_no)

        return {
            "txn_no": txn_no, "merchant": merchant, "customer": customer, "seconds": seconds, "amount": amount,
            "method": method, "brand": brand, "card_type": card_type, "channel": channel, "status": status,
            "reason": reason, "fee": fee, "interchange": interchange, "scheme": scheme,
        }

    def _schedule_refunds(self, month, captured, merchant, cat, seconds, amount, txn_no) -> None:
        rng = self.rng
        rate = np.array([c[8] for c in CATEGORIES])[cat]
        chosen = np.flatnonzero(captured & (rng.random(len(merchant)) < rate))
        if not len(chosen):
            return
        delay_days = np.minimum(rng.geometric(0.16, len(chosen)), 45)
        when = seconds[chosen] + delay_days * 86400 + rng.integers(0, 6 * 3600, len(chosen))
        partial = rng.random(len(chosen)) < 0.35
        value = np.where(partial, np.maximum(1, (amount[chosen] * rng.uniform(0.15, 0.80, len(chosen))).astype(np.int64)),
                         amount[chosen])
        reason = rng.integers(0, len(REFUND_REASONS), len(chosen))
        for i in range(len(chosen)):
            day_abs = int(when[i] // 86400)
            if day_abs > self.total_days - 1:
                continue        # would be refunded after the data ends
            target = self._month_of_day(day_abs)
            self.refund_seq += 1
            self.pending_refunds.setdefault(target, []).append(
                (self.refund_seq, int(txn_no[chosen[i]]), int(when[i]), int(value[i]), int(merchant[chosen[i]]),
                 int(reason[i])))
            self.day_refunds[merchant[chosen[i]], day_abs] += int(value[i])

    def _schedule_chargebacks(self, month, captured, merchant, method, seconds, amount, txn_no) -> None:
        rng = self.rng
        rate = 0.0022 * np.array([1.0, 1.6, 3.5])[self.m_risk[merchant]] * np.where(method == 0, 1.0, 0.25)
        chosen = np.flatnonzero(captured & (rng.random(len(merchant)) < rate))
        for index in chosen:
            day_abs = int(seconds[index] // 86400) + int(rng.integers(20, 100))
            if day_abs > self.total_days - 1:
                continue
            # Recent disputes are more likely to be still open.
            open_share = 0.75 if self.total_days - day_abs < 45 else 0.06
            outcome = "PENDING" if rng.random() < open_share else ("LOST" if rng.random() < 0.55 else "WON")
            self.chargeback_seq += 1
            self.pending_chargebacks.setdefault(self._month_of_day(day_abs), []).append(
                (self.chargeback_seq, int(txn_no[index]), day_abs, int(amount[index]), int(merchant[index]),
                 int(rng.integers(0, len(CHARGEBACK_REASONS))), outcome))
            self.day_chargebacks[merchant[index], day_abs] += int(amount[index])

    def _month_of_day(self, day_abs: int) -> int:
        day = self.first_day + timedelta(days=day_abs)
        return (day.year - self.first_day.year) * 12 + day.month - self.first_day.month

    # ------------------------------------------------------------- file rows

    def _junk_id(self, prefix: str) -> str:
        self.junk_seq += 1
        return f"{prefix}X{self.junk_seq:09d}"

    def _count(self, rate: float, base: int) -> int:
        """How many defects to inject: the expected number, with the fraction decided by chance."""
        expected = rate * base
        return int(expected) + (1 if self.rng.random() < expected - int(expected) else 0)

    def _merchant_rows(self, month: int, defects: Counter) -> list[list[str]]:
        rows = []
        for i in range(self.n_merchants):
            if self.m_join[i] > month:
                continue
            status = "CLOSED" if self.m_churn[i] <= month else "ACTIVE"
            onboarded = month_start(self.cfg.start, int(self.m_join[i]))
            category = CATEGORIES[self.m_cat[i]]
            rows.append([self.m_id[i], self.m_name[i], category[1], category[0], COUNTRIES[self.m_country_idx[i]][0],
                         onboarded.isoformat(), RISK_TIERS[self.m_risk[i]], self._plans[self.m_plan[i]], status])
        if month % 6 == 2 and rows:       # the master file occasionally repeats a merchant
            rows.append(list(rows[int(self.rng.integers(0, len(rows)))]))
            defects[("merchants.csv", "duplicate_merchant")] += 1
        return rows

    def _customer_rows(self, month: int) -> list[list[str]]:
        first = 0 if month == 0 else self._customer_pool(month - 1)
        return [[self.c_id[i], COUNTRIES[self.c_country_idx[i]][0], str(self.c_segment[i])]
                for i in range(first, self._customer_pool(month))]

    @staticmethod
    def _txn_id(numbers: np.ndarray) -> list[str]:
        return [f"T{n:010d}" for n in numbers.tolist()]

    def _transaction_rows(self, t: dict[str, np.ndarray], days: list[date], defects: Counter) -> list[list[str]]:
        rng, rates = self.rng, self.cfg.defects
        n = len(t["txn_no"])
        epoch = np.datetime64(self.first_day.isoformat(), "s")
        stamps = np.datetime_as_string(epoch + t["seconds"].astype("timedelta64[s]"), unit="s").tolist()
        methods = np.array(["CARD", "WALLET", "BANK_TRANSFER"])[t["method"]].tolist()
        brands = np.array(["VISA", "MASTERCARD", "AMEX", "DISCOVER", "APPLE_PAY", "GOOGLE_PAY", "PAYPAL", ""])[t["brand"]].tolist()
        types = np.array(["CREDIT", "DEBIT", "PREPAID", ""])[t["card_type"]].tolist()
        channels = np.array(CHANNELS)[t["channel"]].tolist()
        statuses = np.array(["CAPTURED", "DECLINED", "VOIDED"])[t["status"]].tolist()
        reasons = [DECLINE_REASONS[r] if r >= 0 else "" for r in t["reason"].tolist()]
        merchants = [self.m_id[m] for m in t["merchant"].tolist()]
        currencies = [self.m_currency[m] for m in t["merchant"].tolist()]
        customers = [self.c_id[c] for c in t["customer"].tolist()]

        # Real payments that arrive without a customer id: loaded against the Unknown customer.
        for index in rng.choice(n, size=min(n, self._count(rates.missing_customer, n)), replace=False).tolist():
            customers[index] = ""
            defects[("transactions.csv", "missing_customer")] += 1

        rows = [list(r) for r in zip(self._txn_id(t["txn_no"]), merchants, customers, stamps, fmt_money(t["amount"]),
                                     currencies, methods, brands, types, channels, statuses, reasons, strict=True)]
        if not rows:
            return rows

        def sample() -> list[str]:
            return list(rows[int(rng.integers(0, n))])

        extra: list[list[str]] = []
        for _ in range(self._count(rates.duplicate_transaction, n)):
            extra.append(sample())
            defects[("transactions.csv", "duplicate_transaction_id")] += 1
        junk = [
            (rates.non_positive_amount, "non_positive_amount", lambda r: r.__setitem__(4, rng.choice(["-25.00", "0.00", "-3.99"]))),
            (rates.unknown_currency, "unknown_currency", lambda r: r.__setitem__(5, rng.choice(["XXX", "US$", ""]))),
            (rates.future_timestamp, "future_timestamp",
             lambda r: r.__setitem__(3, f"{days[-1].year + int(rng.integers(1, 4))}-06-15T12:00:00")),
            (rates.malformed_timestamp, "invalid_timestamp",
             lambda r: r.__setitem__(3, rng.choice(["2024-13-45T99:00:00", "yesterday", ""]))),
            (rates.invalid_status, "invalid_status", lambda r: r.__setitem__(10, rng.choice(["PENDING_REVIEW", "OK", ""]))),
        ]
        for rate, name, spoil in junk:
            for _ in range(self._count(rate, n)):
                row = sample()
                row[0] = self._junk_id("T")
                row[10], row[11] = ("DECLINED", "DO_NOT_HONOR") if name != "invalid_status" else (row[10], "")
                spoil(row)
                row[4], row[5], row[3], row[10] = str(row[4]), str(row[5]), str(row[3]), str(row[10])
                extra.append(row)
                defects[("transactions.csv", name)] += 1
        for _ in range(self._count(rates.missing_merchant, n)):       # valid, but nobody knows whose it is
            row = sample()
            row[0], row[1], row[10], row[11] = self._junk_id("T"), "", "DECLINED", "DO_NOT_HONOR"
            extra.append(row)
            defects[("transactions.csv", "missing_merchant")] += 1

        rows.extend(extra)
        rows.sort(key=lambda r: (r[3], r[0]))
        return rows

    def _refund_rows(self, month: int, t: dict[str, np.ndarray], defects: Counter) -> list[list[str]]:
        rng, rates = self.rng, self.cfg.defects
        epoch = np.datetime64(self.first_day.isoformat(), "s")
        rows = []
        for seq, txn_no, when, value, merchant, reason in self.pending_refunds.pop(month, []):
            stamp = str(np.datetime_as_string(epoch + np.timedelta64(when, "s"), unit="s"))
            rows.append([f"R{seq:09d}", f"T{txn_no:010d}", stamp, fmt_money(np.array([value]))[0],
                         self.m_currency[merchant], REFUND_REASONS[reason]])
        n = len(rows)
        if not n:
            return rows
        extra = []
        for _ in range(self._count(rates.duplicate_refund, n)):
            extra.append(list(rows[int(rng.integers(0, n))]))
            defects[("refunds.csv", "duplicate_refund_id")] += 1
        for _ in range(self._count(rates.orphan_refund, n)):
            row = list(rows[int(rng.integers(0, n))])
            row[0], row[1] = self._junk_id("R"), f"T9{int(rng.integers(0, 10**9)):09d}"
            extra.append(row)
            defects[("refunds.csv", "orphan_refund")] += 1
        captured = np.flatnonzero(t["status"] == 0)
        for _ in range(self._count(rates.refund_exceeds_original, n) if len(captured) else 0):
            index = int(captured[int(rng.integers(0, len(captured)))])
            day_abs = int(t["seconds"][index] // 86400)
            stamp = (self.first_day + timedelta(days=day_abs)).isoformat() + "T23:30:00"
            extra.append([self._junk_id("R"), f"T{int(t['txn_no'][index]):010d}", stamp,
                          fmt_money(np.array([int(t["amount"][index]) * 3 + 100]))[0],
                          self.m_currency[int(t["merchant"][index])], "CUSTOMER_REQUEST"])
            defects[("refunds.csv", "refund_exceeds_original")] += 1
        rows.extend(extra)
        rows.sort(key=lambda r: (r[2], r[0]))
        return rows

    def _chargeback_rows(self, month: int, defects: Counter) -> list[list[str]]:
        rng = self.rng
        rows = []
        for seq, txn_no, day_abs, value, merchant, reason, outcome in self.pending_chargebacks.pop(month, []):
            rows.append([f"CB{seq:08d}", f"T{txn_no:010d}", (self.first_day + timedelta(days=day_abs)).isoformat(),
                         fmt_money(np.array([value]))[0], self.m_currency[merchant], CHARGEBACK_REASONS[reason], outcome])
        n = len(rows)
        for _ in range(self._count(self.cfg.defects.orphan_chargeback, n)):
            row = list(rows[int(rng.integers(0, n))])
            row[0], row[1] = self._junk_id("CB"), f"T9{int(rng.integers(0, 10**9)):09d}"
            rows.append(row)
            defects[("chargebacks.csv", "orphan_chargeback")] += 1
        rows.sort(key=lambda r: (r[2], r[0]))
        return rows

    def _fee_rows(self, t: dict[str, np.ndarray], defects: Counter) -> list[list[str]]:
        captured = t["status"] == 0
        merchants = t["merchant"][captured].tolist()
        rows = [list(r) for r in zip(self._txn_id(t["txn_no"][captured]), fmt_money(t["fee"][captured]),
                                     fmt_money(t["interchange"][captured]), fmt_money(t["scheme"][captured]),
                                     [self.m_currency[m] for m in merchants], strict=True)]
        declined = np.flatnonzero(t["status"] == 1)
        for _ in range(self._count(self.cfg.defects.fee_without_capture, len(rows)) if len(declined) else 0):
            index = int(declined[int(self.rng.integers(0, len(declined)))])
            rows.append([f"T{int(t['txn_no'][index]):010d}", "1.20", "0.60", "0.05",
                         self.m_currency[int(t["merchant"][index])]])
            defects[("fees.csv", "fee_without_capture")] += 1
        return rows

    def _settlement_rows(self, days: list[date], day_offset: np.ndarray, defects: Counter) -> list[list[str]]:
        """A payout on day D covers the activity of day D - 1."""
        rng, rates = self.rng, self.cfg.defects
        rows = []
        for settle_day, offset in zip(days, day_offset, strict=True):
            activity = int(offset) - 1
            if activity < 0:
                continue
            gross, fees = self.day_gross[:, activity], self.day_fees[:, activity]
            refunds, chargebacks = self.day_refunds[:, activity], self.day_chargebacks[:, activity]
            for merchant in np.flatnonzero((gross > 0) | (refunds > 0) | (chargebacks > 0)).tolist():
                self.settlement_seq += 1
                net = int(gross[merchant] - fees[merchant] - refunds[merchant] - chargebacks[merchant])
                rows.append([f"S{settle_day:%Y%m%d}-{self.settlement_seq:07d}", self.m_id[merchant], settle_day.isoformat(),
                             self.m_currency[merchant], *fmt_money(np.array([gross[merchant], fees[merchant],
                                                                           refunds[merchant], chargebacks[merchant], net]))])
        n = len(rows)
        for index in rng.choice(n, size=min(n, self._count(rates.settlement_mismatch, n)), replace=False).tolist() if n else []:
            # The payout does not match its own components: an underpayment or an overpayment.
            net_cents = round(float(rows[index][8]) * 100)
            shift = int(rng.integers(50, 25_000)) * (1 if rng.random() < 0.4 else -1)
            rows[index][8] = fmt_money(np.array([net_cents + shift]))[0]
            defects[("settlements.csv", "settlement_mismatch")] += 1
        for _ in range(self._count(rates.duplicate_settlement, n)):
            rows.append(list(rows[int(rng.integers(0, n))]))
            defects[("settlements.csv", "duplicate_settlement_batch_id")] += 1
        return rows

    # ----------------------------------------------------------------- driver

    def run(self) -> GenerationResult:
        for month in range(self.cfg.months):
            batch_id = month_start(self.cfg.start, month).strftime("%Y-%m")
            content = self._month(month)
            write_batch(self.result.out_dir, batch_id, content)
            self.result.batches.append(batch_id)
            for name, rows in content.items():
                self.result.rows[name] += len(rows)
        return self.result


def write_batch(out_dir: Path, batch_id: str, content: dict[str, list[list[str]]]) -> Path:
    """Write one month's files and a manifest of row counts and SHA-256 checksums."""
    folder = Path(out_dir) / batch_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"batch_id": batch_id, "source_system": "finsight-simulator", "files": {}}
    for name, columns in FILES.items():
        rows = content.get(name, [])
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
        payload = buffer.getvalue().encode("utf-8")
        (folder / name).write_bytes(payload)
        manifest["files"][name] = {"rows": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return folder


def generate(config: GeneratorConfig) -> GenerationResult:
    return Generator(config).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic payment processor data")
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 11, 1), help="first day (a month start)")
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--scale", type=float, default=1.0, help="1.0 is about 1.5 million transactions")
    parser.add_argument("--seed", type=int, default=20231101)
    args = parser.parse_args(argv)
    if args.start.day != 1:
        parser.error("--start must be the first day of a month")
    result = generate(GeneratorConfig(args.out, args.start, args.months, args.scale, args.seed))
    print(f"Wrote {len(result.batches)} monthly batches to {result.out_dir} (seed {args.seed}, scale {args.scale})")
    for name, count in result.rows.items():
        print(f"  {name:<18} {count:>10,} rows")
    injected = Counter()
    for (_batch, file_name, defect), count in result.defects.items():
        injected[(file_name, defect)] += count
    print("Defects injected:")
    for (file_name, defect), count in sorted(injected.items()):
        print(f"  {file_name:<18} {defect:<30} {count:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
