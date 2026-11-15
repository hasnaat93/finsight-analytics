-- Settlements, with the reconciliation built in.
--
-- The source says what it paid each merchant. The warehouse independently works out what
-- it should have paid, from its own facts: a payout on day D covers day D - 1, and equals
-- payments captured that day, minus the fees on them, minus refunds and chargebacks dated
-- that day. A batch is reconciled when the two agree to the cent in the settlement currency.

drop table if exists staging.settlements_clean;
create unlogged table staging.settlements_clean as
select
    trim(settlement_batch_id)               as settlement_batch_id,
    trim(merchant_id)                       as merchant_id,
    staging.to_date(settlement_date)        as settlement_date,
    upper(trim(currency))                   as currency_code,
    staging.to_amount(gross_amount)         as gross_amount,
    staging.to_amount(fee_amount)           as fee_amount,
    staging.to_amount(refund_amount)        as refund_amount,
    staging.to_amount(chargeback_amount)    as chargeback_amount,
    staging.to_amount(net_payout)           as net_payout
from staging.settlements_work
where rule is null;

create temporary table expected_payout on commit drop as
with days as (
    select
        to_char(min(settlement_date) - 1, 'YYYYMMDD')::integer as first_key,
        to_char(max(settlement_date) - 1, 'YYYYMMDD')::integer as last_key
    from staging.settlements_clean
),
activity as (
    select m.merchant_id, f.date_key, sum(f.amount_original) as gross, 0::numeric as fees,
           0::numeric as refunds, 0::numeric as chargebacks
    from dwh.fact_transaction f
    join dwh.dim_merchant m on m.merchant_key = f.merchant_key
    cross join days
    where f.is_captured and f.date_key between days.first_key and days.last_key
    group by m.merchant_id, f.date_key
    union all
    select m.merchant_id, f.date_key, 0, sum(f.fee_original), 0, 0
    from dwh.fact_fee f
    join dwh.dim_merchant m on m.merchant_key = f.merchant_key
    cross join days
    where f.date_key between days.first_key and days.last_key
    group by m.merchant_id, f.date_key
    union all
    select m.merchant_id, f.date_key, 0, 0, sum(f.refund_original), 0
    from dwh.fact_refund f
    join dwh.dim_merchant m on m.merchant_key = f.merchant_key
    cross join days
    where f.date_key between days.first_key and days.last_key
    group by m.merchant_id, f.date_key
    union all
    select m.merchant_id, f.date_key, 0, 0, 0, sum(f.chargeback_original)
    from dwh.fact_chargeback f
    join dwh.dim_merchant m on m.merchant_key = f.merchant_key
    cross join days
    where f.date_key between days.first_key and days.last_key
    group by m.merchant_id, f.date_key
)
select
    merchant_id,
    date_key as activity_date_key,
    sum(gross) - sum(fees) - sum(refunds) - sum(chargebacks) as expected_payout
from activity
group by merchant_id, date_key;

insert into dwh.fact_settlement (
    settlement_batch_id, date_key, merchant_key, geography_key, currency_key,
    gross_original, fees_original, refunds_original, chargebacks_original, net_payout_original,
    expected_payout_original, gross_amount, fees, refunds, chargebacks, net_payout, expected_payout,
    is_reconciled, batch_id)
select
    s.settlement_batch_id,
    to_char(s.settlement_date, 'YYYYMMDD')::integer,
    m.merchant_key,
    coalesce(g.geography_key, -1),
    coalesce(cur.currency_key, -1),
    s.gross_amount, s.fee_amount, s.refund_amount, s.chargeback_amount, s.net_payout,
    coalesce(e.expected_payout, 0),
    round(s.gross_amount * coalesce(fx.rate_to_usd, 1), 2),
    round(s.fee_amount * coalesce(fx.rate_to_usd, 1), 2),
    round(s.refund_amount * coalesce(fx.rate_to_usd, 1), 2),
    round(s.chargeback_amount * coalesce(fx.rate_to_usd, 1), 2),
    round(s.net_payout * coalesce(fx.rate_to_usd, 1), 2),
    round(coalesce(e.expected_payout, 0) * coalesce(fx.rate_to_usd, 1), 2),
    s.net_payout = coalesce(e.expected_payout, 0),
    %(batch_id)s
from staging.settlements_clean s
join dwh.dim_merchant m
  on m.merchant_id = s.merchant_id
 and s.settlement_date >= m.valid_from and s.settlement_date < m.valid_to
left join dwh.dim_geography g on g.country_code = m.country_code
left join dwh.dim_currency cur on cur.currency_code = s.currency_code
left join lateral (
    select x.rate_to_usd from dwh.fx_rate x
    where x.currency_key = cur.currency_key and x.rate_date <= s.settlement_date
    order by x.rate_date desc limit 1
) fx on true
left join expected_payout e
  on e.merchant_id = s.merchant_id
 and e.activity_date_key = to_char(s.settlement_date - 1, 'YYYYMMDD')::integer
on conflict (settlement_batch_id) do nothing;
