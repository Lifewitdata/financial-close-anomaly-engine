{#
  One ERP feed, two engines.

  In Databricks prod the ERP extract lands as an external Delta Bronze table declared
  in models/staging/_staging__sources.yml, and dbt reads it through source().
  Locally the identical feed is mirrored by dbt seeds, so a reviewer reproduces the
  whole build with no cloud account.

  Only the branch that matches the active target is rendered, so the DuckDB build never
  resolves the Databricks source (and never needs a Databricks connection).
#}

{% macro erp(table_name) -%}
  {%- if target.name == 'databricks' -%}
    {{ source('erp', table_name) }}
  {%- else -%}
    {{ ref(table_name) }}
  {%- endif -%}
{%- endmacro %}
