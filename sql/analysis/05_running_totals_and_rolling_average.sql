-- Daily volume with a 7-day rolling average, month-to-date and year-to-date running totals.
-- Techniques: window frames (ROWS BETWEEN), partitioned running totals.
with daily as (
    select d.full_date, d.year, d.year_month, sum(s.tpv) as tpv
    from mart.daily_merchant_summary s
    join dwh.dim_date d on d.date_key = s.date_key
    group by d.full_date, d.year, d.year_month
)
select
    full_date,
    tpv,
    round(avg(tpv) over (order by full_date rows between 6 preceding and current row), 2)  as tpv_7_day_avg,
    sum(tpv) over (partition by year_month order by full_date)                             as tpv_month_to_date,
    sum(tpv) over (partition by year order by full_date)                                   as tpv_year_to_date,
    round(100.0 * (tpv - lag(tpv, 7) over (order by full_date)) / nullif(lag(tpv, 7) over (order by full_date), 0), 2)
                                                                                           as change_vs_same_day_last_week_pct
from daily
order by full_date;
