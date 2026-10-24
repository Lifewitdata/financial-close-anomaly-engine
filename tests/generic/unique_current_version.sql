{% test unique_current_version(model, column_name) %}

{#
  SCD2 integrity gate: history can have many rows per key, but at most one of them may be
  the live version. If a merge/snapshot bug ever leaves two open rows for the same key,
  every as-of join silently doubles.
#}

select {{ column_name }} as key
from {{ model }}
where dbt_valid_to is null
group by {{ column_name }}
having count(*) > 1

{% endtest %}
