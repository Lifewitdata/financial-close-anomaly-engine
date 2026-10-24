{% macro fiscal_period(date_col) %}
  -- Fiscal period label e.g. 2026-03, plus month start for grouping
  cast({{ date_col }} as varchar) -- replaced below in models with date_trunc where dialect differs
{% endmacro %}
{% macro month_start(date_col) %}
  date_trunc('month', {{ date_col }})
{% endmacro %}
