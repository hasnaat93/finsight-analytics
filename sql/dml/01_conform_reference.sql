-- Cleanse and conform: turn the staged text rows that passed validation into typed,
-- standardised rows. One *_clean table per entity, rebuilt for every batch.
--
-- Standardisation: trimmed, codes upper-cased, labels in title case, blanks replaced
-- by an explicit value, so the same thing is never spelled two ways in a report.

drop table if exists staging.merchants_clean;
create unlogged table staging.merchants_clean as
select
    trim(merchant_id)                                           as merchant_id,
    trim(merchant_name)                                         as merchant_name,
    coalesce(nullif(trim(category), ''), 'Unclassified')        as category,
    coalesce(nullif(trim(mcc), ''), '0000')                     as mcc,
    upper(trim(country))                                        as country_code,
    initcap(coalesce(nullif(trim(risk_tier), ''), 'Unknown'))   as risk_tier,
    initcap(coalesce(nullif(trim(pricing_plan), ''), 'Unknown')) as pricing_plan,
    initcap(coalesce(nullif(trim(status), ''), 'Unknown'))      as status,
    staging.to_date(onboarding_date)                            as onboarding_date
from staging.merchants_work
where rule is null;

drop table if exists staging.customers_clean;
create unlogged table staging.customers_clean as
select
    trim(customer_id)                                           as customer_hash,
    case when upper(trim(country)) in (select country_code from dwh.dim_geography)
         then upper(trim(country)) else 'ZZ' end                as country_code,
    initcap(coalesce(nullif(trim(segment), ''), 'Unknown'))     as segment
from staging.customers_work
where rule is null;

drop table if exists staging.fx_rates_clean;
create unlogged table staging.fx_rates_clean as
select
    staging.to_date(rate_date)          as rate_date,
    upper(trim(currency))               as currency_code,
    staging.to_number(rate_to_usd)      as rate_to_usd
from staging.fx_rates_work
where rule is null;
