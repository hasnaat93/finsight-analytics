-- Month-over-month growth and monthly rank of every merchant.
-- Techniques: CTE, LAG for the previous month, RANK within each month, named WINDOW.
with monthly as (
    select
        m.merchant_id,
        max(m.merchant_name) filter (where m.is_current) as merchant_name,
        d.year_month,
        sum(s.tpv) as tpv
    from mart.daily_merchant_summary s
    join dwh.dim_merchant m on m.merchant_key = s.merchant_key
    join dwh.dim_date d on d.date_key = s.date_key
    where m.merchant_key <> -1
    group by m.merchant_id, d.year_month
)
select
    merchant_id,
    merchant_name,
    year_month,
    tpv,
    lag(tpv) over by_merchant                                                   as previous_month_tpv,
    round(100.0 * (tpv - lag(tpv) over by_merchant)
          / nullif(lag(tpv) over by_merchant, 0), 2)                            as mom_growth_pct,
    rank() over (partition by year_month order by tpv desc)                     as tpv_rank
from monthly
window by_merchant as (partition by merchant_id order by year_month)
order by year_month, tpv_rank;
