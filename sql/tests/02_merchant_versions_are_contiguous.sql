-- Versions of a merchant neither overlap nor leave gaps, and span 1900-01-01 to 9999-12-31.
select merchant_id, valid_from, valid_to
from (
    select
        merchant_id, valid_from, valid_to,
        lag(valid_to)    over w as previous_valid_to,
        lead(valid_from) over w as next_valid_from
    from dwh.dim_merchant
    window w as (partition by merchant_id order by valid_from)
) versions
where valid_from <> coalesce(previous_valid_to, date '1900-01-01')
   or valid_to   <> coalesce(next_valid_from, date '9999-12-31')
