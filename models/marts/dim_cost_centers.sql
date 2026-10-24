-- Gold: conformed dimension. Grain: cost_center_id. SCD Type 1 in this portfolio build;
-- prod note: switch to dbt snapshots for Type 2 history before IPO audit.
select
    cast(cost_center_id as varchar) as cost_center_id,
    trim(cast(cost_center_name as varchar)) as cost_center_name,
    trim(cast(region as varchar)) as region,
    current_timestamp as _updated_at
from {{ ref('raw_cost_centers') }}
