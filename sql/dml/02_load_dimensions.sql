-- Dimension loads that are simple upserts. All are safe to repeat.

select dwh.populate_dim_date(
    (cast(%(batch_start)s as date) - interval '1 month')::date,
    (cast(%(batch_end)s as date) + interval '2 months')::date);

-- Exchange rates: a re-delivered rate replaces the earlier one.
insert into dwh.fx_rate (rate_date, currency_key, rate_to_usd)
select f.rate_date, c.currency_key, f.rate_to_usd
from staging.fx_rates_clean f
join dwh.dim_currency c on c.currency_code = f.currency_code
on conflict (currency_key, rate_date) do update set rate_to_usd = excluded.rate_to_usd;

-- Customers: type 1. A changed country or segment overwrites the old value.
insert into dwh.dim_customer (customer_hash, country_code, segment)
select customer_hash, country_code, segment
from staging.customers_clean
on conflict (customer_hash) do update
set country_code = excluded.country_code, segment = excluded.segment
where (dwh.dim_customer.country_code, dwh.dim_customer.segment)
      is distinct from (excluded.country_code, excluded.segment);
