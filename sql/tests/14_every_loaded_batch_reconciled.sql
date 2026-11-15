-- No successful load has a failed control, and every one has controls recorded.
select e.batch_id
from audit.etl_run e
left join audit.reconciliation c on c.run_id = e.run_id
where e.status = 'SUCCESS'
group by e.batch_id
having count(c.*) = 0 or count(*) filter (where c.status = 'FAIL') > 0
