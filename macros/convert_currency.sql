{% macro convert_to_usd(amount_col, currency_col) %}
  -- Centralised FX logic: one place to audit, used by every downstream model.
  -- Rates live in seeds/fx_rates.csv so Finance can version-control them.
  {{ amount_col }} * coalesce(
    (select rate_to_usd from {{ ref('fx_rates') }} fx where fx.currency = {{ currency_col }} limit 1),
    1.0
  )
{% endmacro %}
