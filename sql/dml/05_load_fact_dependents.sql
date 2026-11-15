-- Refunds, chargebacks and fees. These are validated after the payments of the batch are
-- in the warehouse, because their rules look the payment up. Each inherits its merchant,
-- geography and currency from that payment.

drop table if exists staging.refunds_clean;
create unlogged table staging.refunds_clean as
select
    trim(refund_id)                                                         as refund_id,
    trim(transaction_id)                                                    as transaction_id,
    staging.to_ts(refund_ts)                                                as refund_ts,
    staging.to_amount(amount)                                               as amount,
    initcap(replace(coalesce(nullif(trim(reason), ''), 'Unspecified'), '_', ' ')) as refund_reason
from staging.refunds_work
where rule is null;

drop table if exists staging.chargebacks_clean;
create unlogged table staging.chargebacks_clean as
select
    trim(chargeback_id)                                                     as chargeback_id,
    trim(transaction_id)                                                    as transaction_id,
    staging.to_date(chargeback_date)                                        as chargeback_date,
    staging.to_amount(amount)                                               as amount,
    initcap(replace(coalesce(nullif(trim(reason_code), ''), 'Unspecified'), '_', ' ')) as reason_code,
    upper(trim(outcome))                                                    as outcome
from staging.chargebacks_work
where rule is null;

drop table if exists staging.fees_clean;
create unlogged table staging.fees_clean as
select
    trim(transaction_id)                    as transaction_id,
    staging.to_amount(merchant_fee)         as merchant_fee,
    staging.to_amount(interchange_cost)     as interchange_cost,
    staging.to_amount(scheme_cost)          as scheme_cost
from staging.fees_work
where rule is null;

-- A refund or chargeback is converted at the rate of its own date.
insert into dwh.fact_refund (refund_id, transaction_key, date_key, merchant_key, geography_key, currency_key,
                             refund_reason, refund_original, refund_amount, is_partial, batch_id)
select
    r.refund_id, f.transaction_key, to_char(r.refund_ts, 'YYYYMMDD')::integer,
    f.merchant_key, f.geography_key, f.currency_key, r.refund_reason,
    r.amount, round(r.amount * fx.rate_to_usd, 2), r.amount < f.amount_original, %(batch_id)s
from staging.refunds_clean r
join dwh.fact_transaction f on f.transaction_id = r.transaction_id
join lateral (
    select x.rate_to_usd from dwh.fx_rate x
    where x.currency_key = f.currency_key and x.rate_date <= r.refund_ts::date
    order by x.rate_date desc limit 1
) fx on true
on conflict (refund_id) do nothing;

insert into dwh.fact_chargeback (chargeback_id, transaction_key, date_key, merchant_key, geography_key,
                                 currency_key, reason_code, outcome, chargeback_original, chargeback_amount, batch_id)
select
    c.chargeback_id, f.transaction_key, to_char(c.chargeback_date, 'YYYYMMDD')::integer,
    f.merchant_key, f.geography_key, f.currency_key, c.reason_code, c.outcome,
    c.amount, round(c.amount * fx.rate_to_usd, 2), %(batch_id)s
from staging.chargebacks_clean c
join dwh.fact_transaction f on f.transaction_id = c.transaction_id
join lateral (
    select x.rate_to_usd from dwh.fx_rate x
    where x.currency_key = f.currency_key and x.rate_date <= c.chargeback_date
    order by x.rate_date desc limit 1
) fx on true
on conflict (chargeback_id) do nothing;

-- A fee is converted at the same rate as its payment, so fee / payment is the same
-- percentage in either currency.
insert into dwh.fact_fee (transaction_key, date_key, merchant_key, geography_key, currency_key,
                          fee_original, fee_amount, interchange_cost, scheme_cost, batch_id)
select
    f.transaction_key, f.date_key, f.merchant_key, f.geography_key, f.currency_key,
    s.merchant_fee,
    round(s.merchant_fee * fx.rate_to_usd, 2),
    round(s.interchange_cost * fx.rate_to_usd, 2),
    round(s.scheme_cost * fx.rate_to_usd, 2),
    %(batch_id)s
from staging.fees_clean s
join dwh.fact_transaction f on f.transaction_id = s.transaction_id
join lateral (
    select x.rate_to_usd from dwh.fx_rate x
    where x.currency_key = f.currency_key and x.rate_date <= f.transaction_ts::date
    order by x.rate_date desc limit 1
) fx on true
on conflict (transaction_key) do nothing;
