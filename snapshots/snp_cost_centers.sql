{% snapshot snp_cost_centers %}

{#
  SCD Type 2 history for the cost centre master.

  Cost centres get renamed and re-parented between regions, and a close reported against
  "Sales / North America" must still read that way after the org chart moves.
  dim_cost_centers stays the current-state dimension (Type 1); this table is the audit
  trail, so any GL line can be joined to the version that was active on its date:

      join {{ ref('snp_cost_centers') }} c
        on c.cost_center_id = g.cost_center_id
       and g.transaction_date >= c.dbt_valid_from
       and (g.transaction_date < c.dbt_valid_to or c.dbt_valid_to is null)

  strategy='check' because the ERP feed carries no reliable last-changed timestamp: a new
  version is written when a non-key column actually changes, not on every reload.
  scripts/verify_scd2_history.py runs both phases and commits the resulting history.
#}

{% if target.name == 'databricks' %}
  {{ config(alias='dim_cost_centers_history',
            unique_key='cost_center_id', strategy='check',
            check_cols=['cost_center_name', 'region']) }}
{% else %}
  {{ config(alias='dim_cost_centers_history',
            unique_key='cost_center_id', strategy='check',
            check_cols=['cost_center_name', 'region'],
            snapshot_config={'ts1': 'TIMESTAMP', 'ts2': 'TIMESTAMP'}) }}
{% endif %}

select
    cast(cost_center_id as varchar)         as cost_center_id,
    trim(cast(cost_center_name as varchar)) as cost_center_name,
    trim(cast(region as varchar))           as region
from {{ erp('raw_cost_centers') }}

{% endsnapshot %}
