-- Cohort retention: of the merchants whose first captured payment fell in a given month,
-- what share was still trading N months later?
-- Techniques: chained CTEs, date arithmetic, FIRST_VALUE to size each cohort.
with merchant_months as (
    select distinct m.merchant_id, date_trunc('month', d.full_date)::date as activity_month
    from mart.daily_merchant_summary s
    join dwh.dim_merchant m on m.merchant_key = s.merchant_key
    join dwh.dim_date d on d.date_key = s.date_key
    where s.captured_transactions > 0 and m.merchant_key <> -1
),
cohorts as (
    select merchant_id, min(activity_month) as cohort_month
    from merchant_months
    group by merchant_id
),
retention as (
    select
        c.cohort_month,
        (extract(year from age(a.activity_month, c.cohort_month)) * 12
         + extract(month from age(a.activity_month, c.cohort_month)))::integer as months_since_first,
        count(*) as active_merchants
    from cohorts c
    join merchant_months a using (merchant_id)
    group by 1, 2
)
select
    cohort_month,
    months_since_first,
    active_merchants,
    first_value(active_merchants) over by_cohort                               as cohort_size,
    round(100.0 * active_merchants / first_value(active_merchants) over by_cohort, 1) as retention_pct
from retention
window by_cohort as (partition by cohort_month order by months_since_first)
order by cohort_month, months_since_first;
