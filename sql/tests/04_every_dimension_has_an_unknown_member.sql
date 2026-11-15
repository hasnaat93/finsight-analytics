-- Key -1 exists in every dimension that facts can fall back to.
select missing.dimension
from (values
    ('dim_merchant',           (select count(*) from dwh.dim_merchant where merchant_key = -1)),
    ('dim_customer',           (select count(*) from dwh.dim_customer where customer_key = -1)),
    ('dim_geography',          (select count(*) from dwh.dim_geography where geography_key = -1)),
    ('dim_channel',            (select count(*) from dwh.dim_channel where channel_key = -1)),
    ('dim_payment_method',     (select count(*) from dwh.dim_payment_method where method_key = -1)),
    ('dim_currency',           (select count(*) from dwh.dim_currency where currency_key = -1)),
    ('dim_transaction_status', (select count(*) from dwh.dim_transaction_status where status_key = -1))
) as missing(dimension, members)
where missing.members <> 1
