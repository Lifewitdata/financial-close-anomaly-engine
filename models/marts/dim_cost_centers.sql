-- Gold: conformed dimension, current state (SCD Type 1) for "where does this cost centre
-- roll up NOW". History is not lost: snapshots/snp_cost_centers.sql materialises the SCD
-- Type 2 trail as gold_scd.dim_cost_centers_history, and that is the table an auditor
-- wants when asking what a line rolled up to on its own transaction_date.
select
    cast(cost_center_id as varchar)         as cost_center_id,
    trim(cast(cost_center_name as varchar)) as cost_center_name,
    trim(cast(region as varchar))           as region,
    current_timestamp                       as _updated_at
from {{ erp('raw_cost_centers') }}
