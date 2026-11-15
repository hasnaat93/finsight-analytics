-- Slowly changing dimension, type 2, from the merchant master file of the month.
-- A change takes effect on the first day of the batch that delivered it.
-- Must be applied in batch order; the pipeline skips it when reloading an older batch.

create temporary table merchant_snapshot on commit drop as
select
    s.*,
    md5(concat_ws('|', category, mcc, country_code, risk_tier, pricing_plan, status)) as row_hash
from staging.merchants_clean s;

-- 1. Type 1: a corrected name overwrites every version.
update dwh.dim_merchant d
set merchant_name = s.merchant_name
from merchant_snapshot s
where d.merchant_id = s.merchant_id
  and d.merchant_name <> s.merchant_name;

-- 2. Expire the current version of every merchant whose tracked attributes changed.
update dwh.dim_merchant d
set valid_to = cast(%(batch_start)s as date),
    is_current = false
from merchant_snapshot s
where d.merchant_id = s.merchant_id
  and d.is_current
  and d.row_hash <> s.row_hash;

-- 3. Insert a version for every merchant that now has no current row: new merchants and
--    those expired in step 2. The first version of a merchant is valid from the beginning
--    of time, so a payment dated before the merchant was first delivered still resolves.
insert into dwh.dim_merchant (merchant_id, merchant_name, category, mcc, country_code, risk_tier, pricing_plan,
                              status, onboarding_date, valid_from, valid_to, is_current, row_hash)
select
    s.merchant_id, s.merchant_name, s.category, s.mcc, s.country_code, s.risk_tier, s.pricing_plan,
    s.status, s.onboarding_date,
    case when exists (select 1 from dwh.dim_merchant p where p.merchant_id = s.merchant_id)
         then cast(%(batch_start)s as date) else date '1900-01-01' end,
    date '9999-12-31',
    true,
    s.row_hash
from merchant_snapshot s
where not exists (select 1 from dwh.dim_merchant d where d.merchant_id = s.merchant_id and d.is_current);
