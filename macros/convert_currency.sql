{#
  Centralised FX logic: one place to audit, used by every model that needs USD.
  Rates live in seeds/fx_rates.csv so Finance can version-control them, and models join
  that one seed (alias it `fx`) instead of each carrying their own conversion maths.

  An unmapped currency falls back to 1.0 (assumed reporting currency). That would
  silently mis-state every USD number, so tests/assert_no_missing_fx_rate.sql turns it
  into a build error rather than a quiet wrong figure.
#}

{% macro convert_to_usd(amount_col, rate_col='fx.rate_to_usd') -%}
  round({{ amount_col }} * coalesce({{ rate_col }}, 1.0), 2)
{%- endmacro %}

{% macro fx_rate(rate_col='fx.rate_to_usd') -%}
  coalesce({{ rate_col }}, 1.0)
{%- endmacro %}
