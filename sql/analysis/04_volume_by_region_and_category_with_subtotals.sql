-- Volume, approval rate and take rate by region and category, with subtotals and a grand total.
-- Techniques: GROUPING SETS for the subtotal rows, GROUPING() to label them, FILTER.
select
    case when grouping(g.region) = 1 then 'All regions' else g.region end           as region,
    case when grouping(m.category) = 1 then 'All categories' else m.category end    as category,
    count(*)                                                                        as transactions,
    round(100.0 * count(*) filter (where f.is_approved) / count(*), 2)              as approval_rate_pct,
    sum(f.amount_reporting) filter (where f.is_captured)                            as tpv,
    round(100.0 * sum(fee.fee_amount) / nullif(sum(f.amount_reporting) filter (where f.is_captured), 0), 3) as take_rate_pct
from dwh.fact_transaction f
join dwh.dim_merchant m on m.merchant_key = f.merchant_key
join dwh.dim_geography g on g.geography_key = f.geography_key
left join dwh.fact_fee fee on fee.transaction_key = f.transaction_key
where f.merchant_key <> -1
group by grouping sets ((g.region, m.category), (g.region), (m.category), ())
order by grouping(g.region), g.region, grouping(m.category), tpv desc;
