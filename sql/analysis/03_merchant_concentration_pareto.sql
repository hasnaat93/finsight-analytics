-- Pareto analysis: how few merchants account for how much of the volume?
-- Techniques: running total with SUM() OVER, PERCENT_RANK, cumulative share.
with merchant_tpv as (
    select m.merchant_id, max(m.merchant_name) filter (where m.is_current) as merchant_name, sum(s.tpv) as tpv
    from mart.daily_merchant_summary s
    join dwh.dim_merchant m on m.merchant_key = s.merchant_key
    where m.merchant_key <> -1
    group by m.merchant_id
)
select
    row_number() over (order by tpv desc)                                                   as tpv_rank,
    merchant_id,
    merchant_name,
    tpv,
    round(100.0 * tpv / sum(tpv) over (), 2)                                                as share_pct,
    round(100.0 * sum(tpv) over (order by tpv desc rows unbounded preceding) / sum(tpv) over (), 2) as cumulative_share_pct,
    round(100.0 * (1 - percent_rank() over (order by tpv)), 2)                              as top_percent_of_merchants
from merchant_tpv
order by tpv_rank;
