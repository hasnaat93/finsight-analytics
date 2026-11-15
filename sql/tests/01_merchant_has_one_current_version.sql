-- Every merchant has exactly one current version in the type 2 dimension.
select merchant_id
from dwh.dim_merchant
group by merchant_id
having count(*) filter (where is_current) <> 1
