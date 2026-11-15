-- The mart schema: the only thing Power BI reads.
--
-- Each view exposes one table of the star schema with just the columns a report needs.
-- Load metadata (batch ids, row hashes, identity keys of facts) stays in dwh. Because the
-- model imports views, the warehouse tables can be reorganised without breaking a report.

-- ------------------------------------------------------------- dimensions

create or replace view mart.dim_date as
select
    date_key,
    full_date           as date,
    year,
    quarter_number,
    quarter_label       as quarter,
    month_number,
    month_name,
    year_month,
    week_of_year,
    day_of_month,
    day_of_week,
    day_name,
    fiscal_year,
    fiscal_quarter,
    fiscal_period,
    is_weekend,
    is_holiday
from dwh.dim_date
where full_date between (select min(d.full_date) from dwh.fact_transaction f join dwh.dim_date d using (date_key))
                    and (select date_trunc('year', max(d.full_date)) + interval '1 year - 1 day'
                         from dwh.fact_transaction f join dwh.dim_date d using (date_key));

create or replace view mart.dim_geography as
select geography_key, country_code, country_name as country, region, continent
from dwh.dim_geography;

create or replace view mart.dim_merchant as
select
    m.merchant_key,
    m.merchant_id,
    m.merchant_name,
    m.category,
    m.mcc,
    g.country_name      as country,
    g.region,
    m.risk_tier,
    m.pricing_plan,
    m.status,
    m.onboarding_date,
    m.valid_from,
    m.valid_to,
    m.is_current
from dwh.dim_merchant m
left join dwh.dim_geography g on g.country_code = m.country_code;

create or replace view mart.dim_customer as
select c.customer_key, c.segment, g.country_name as country
from dwh.dim_customer c
left join dwh.dim_geography g on g.country_code = c.country_code;

create or replace view mart.dim_payment_method as
select method_key, payment_method, card_brand, card_type
from dwh.dim_payment_method;

create or replace view mart.dim_channel as
select channel_key, channel_name as channel
from dwh.dim_channel;

create or replace view mart.dim_currency as
select currency_key, currency_code, currency_name
from dwh.dim_currency;

create or replace view mart.dim_transaction_status as
select status_key, status, decline_reason, is_success
from dwh.dim_transaction_status;

-- ------------------------------------------------------------------ facts

create or replace view mart.fact_transaction as
select
    transaction_key, date_key, merchant_key, customer_key, method_key, channel_key, geography_key,
    currency_key, status_key, hour_of_day, amount_original, amount_reporting, is_approved, is_captured
from dwh.fact_transaction;

create or replace view mart.fact_refund as
select refund_key, date_key, merchant_key, geography_key, currency_key, refund_reason, refund_amount, is_partial
from dwh.fact_refund;

create or replace view mart.fact_chargeback as
select chargeback_key, date_key, merchant_key, geography_key, currency_key, reason_code, outcome, chargeback_amount
from dwh.fact_chargeback;

create or replace view mart.fact_fee as
select fee_key, date_key, merchant_key, geography_key, currency_key, fee_amount, interchange_cost, scheme_cost
from dwh.fact_fee;

create or replace view mart.fact_settlement as
select
    settlement_key, settlement_batch_id, date_key, merchant_key, geography_key, currency_key,
    gross_amount, fees, refunds, chargebacks, net_payout, expected_payout, variance, is_reconciled
from dwh.fact_settlement;

-- ------------------------------------------------------- pre-aggregated mart

-- One row per day and merchant version. Reports that do not need payment-level detail
-- read this instead of 1.5 million fact rows. Refreshed at the end of every pipeline run.
create materialized view if not exists mart.daily_merchant_summary as
with payments as (
    select
        date_key, merchant_key,
        count(*)                                        as transactions,
        count(*) filter (where is_approved)             as approved_transactions,
        count(*) filter (where is_captured)             as captured_transactions,
        sum(amount_reporting) filter (where is_captured) as tpv
    from dwh.fact_transaction
    group by date_key, merchant_key
),
fees as (
    select date_key, merchant_key, sum(fee_amount) as fee_revenue,
           sum(interchange_cost + scheme_cost) as processing_cost
    from dwh.fact_fee group by date_key, merchant_key
),
refunds as (
    select date_key, merchant_key, count(*) as refund_count, sum(refund_amount) as refund_amount
    from dwh.fact_refund group by date_key, merchant_key
),
chargebacks as (
    select date_key, merchant_key, count(*) as chargeback_count, sum(chargeback_amount) as chargeback_amount,
           sum(chargeback_amount) filter (where outcome = 'LOST') as chargeback_losses
    from dwh.fact_chargeback group by date_key, merchant_key
),
keys as (
    select date_key, merchant_key from payments
    union select date_key, merchant_key from refunds
    union select date_key, merchant_key from chargebacks
)
select
    k.date_key,
    k.merchant_key,
    coalesce(p.transactions, 0)             as transactions,
    coalesce(p.approved_transactions, 0)    as approved_transactions,
    coalesce(p.captured_transactions, 0)    as captured_transactions,
    coalesce(p.tpv, 0)                      as tpv,
    coalesce(f.fee_revenue, 0)              as fee_revenue,
    coalesce(f.processing_cost, 0)          as processing_cost,
    coalesce(r.refund_count, 0)             as refund_count,
    coalesce(r.refund_amount, 0)            as refund_amount,
    coalesce(c.chargeback_count, 0)         as chargeback_count,
    coalesce(c.chargeback_amount, 0)        as chargeback_amount,
    coalesce(c.chargeback_losses, 0)        as chargeback_losses
from keys k
left join payments p using (date_key, merchant_key)
left join fees f using (date_key, merchant_key)
left join refunds r using (date_key, merchant_key)
left join chargebacks c using (date_key, merchant_key);

create unique index if not exists daily_merchant_summary_key on mart.daily_merchant_summary (date_key, merchant_key);
create index if not exists daily_merchant_summary_merchant on mart.daily_merchant_summary (merchant_key);

-- ------------------------------------------------------- data quality marts

-- Rows the pipeline quarantined, by batch, file and rule. Feeds the data quality panel.
create or replace view mart.etl_rejections as
select
    r.batch_id,
    to_date(r.batch_id || '-01', 'YYYY-MM-DD')  as batch_month,
    r.entity,
    r.rule_name,
    max(r.reason)                               as reason,
    count(*)                                    as rejected_rows
from audit.rejected_rows r
group by r.batch_id, r.entity, r.rule_name;

-- One row per loaded batch: when it ran and how its rows were accounted for.
create or replace view mart.etl_batches as
select
    e.batch_id,
    to_date(e.batch_id || '-01', 'YYYY-MM-DD')  as batch_month,
    e.finished_at                               as loaded_at,
    e.duration_ms,
    (select sum(f.row_count) from audit.etl_file f where f.run_id = e.run_id)               as rows_delivered,
    (select count(*) from audit.rejected_rows r where r.run_id = e.run_id)                  as rows_rejected,
    (select count(*) from audit.reconciliation c where c.run_id = e.run_id)                 as controls_run,
    (select count(*) from audit.reconciliation c where c.run_id = e.run_id and c.status = 'PASS') as controls_passed
from audit.etl_run e
where e.status = 'SUCCESS';

create or replace view mart.reconciliation_controls as
select c.batch_id, c.entity, c.check_name, c.source_value, c.target_value, c.difference, c.status
from audit.reconciliation c
join audit.etl_run e on e.run_id = c.run_id and e.status = 'SUCCESS';

-- ----------------------------------------------------- row-level security

-- Who may see which region. The Power BI role "Regional Manager" filters dim_geography
-- to the regions listed here for the signed-in user. The addresses are placeholders.
create table if not exists dwh.region_access (
    user_email  varchar(120) not null,
    region      varchar(40)  not null,
    primary key (user_email, region)
);

insert into dwh.region_access (user_email, region) values
    ('manager.americas@example.com', 'North America'),
    ('manager.europe@example.com', 'Western Europe'),
    ('manager.europe@example.com', 'Southern Europe'),
    ('manager.europe@example.com', 'UK & Ireland'),
    ('manager.asia@example.com', 'South Asia'),
    ('manager.asia@example.com', 'Middle East'),
    ('manager.asia@example.com', 'South East Asia'),
    ('manager.asia@example.com', 'Oceania')
on conflict do nothing;

create or replace view mart.region_access as
select user_email, region from dwh.region_access;
