-- Merchants whose volume in the latest 12 months fell by more than 25% against the 12
-- months before. Techniques: conditional aggregation over two periods, HAVING.
with bounds as (
    select max(d.full_date) as last_day
    from mart.daily_merchant_summary s join dwh.dim_date d on d.date_key = s.date_key
),
periods as (
    select
        m.merchant_id,
        max(m.merchant_name) filter (where m.is_current)    as merchant_name,
        max(m.category) filter (where m.is_current)         as category,
        sum(s.tpv) filter (where d.full_date >  b.last_day - interval '12 months')   as tpv_last_12m,
        sum(s.tpv) filter (where d.full_date <= b.last_day - interval '12 months'
                             and d.full_date >  b.last_day - interval '24 months')   as tpv_prior_12m
    from mart.daily_merchant_summary s
    join dwh.dim_merchant m on m.merchant_key = s.merchant_key
    join dwh.dim_date d on d.date_key = s.date_key
    cross join bounds b
    where m.merchant_key <> -1
    group by m.merchant_id
)
select
    merchant_id, merchant_name, category,
    coalesce(tpv_last_12m, 0)   as tpv_last_12m,
    tpv_prior_12m,
    round(100.0 * (coalesce(tpv_last_12m, 0) - tpv_prior_12m) / tpv_prior_12m, 1) as change_pct
from periods
where tpv_prior_12m > 0
  and coalesce(tpv_last_12m, 0) < 0.75 * tpv_prior_12m
order by tpv_prior_12m - coalesce(tpv_last_12m, 0) desc;
