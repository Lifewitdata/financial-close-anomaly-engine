{#
  Gold: fact table for reporting, built as an INCREMENTAL MERGE on line_id.

  Why merge and not full refresh: a close must be reproducible and cheap. The ERP
  delivers the open period repeatedly (late postings, reversals, corrections) while
  closed periods are frozen. So only rows in the newest fiscal month already in the
  table are re-merged; anything Finance signed off earlier keeps the exact rows it was
  signed off with. scripts/verify_incremental_merge.py proves all four branches:
  initial load, open-period re-merge, new-row insert, matched-row update.
#}

{% if target.name == 'databricks' %}
  {{ config(materialized='incremental', unique_key='line_id', incremental_strategy='merge',
             on_schema_change='fail', partition_by='fiscal_month', cluster_by='account_id') }}
{% else %}
  {{ config(materialized='incremental', unique_key='line_id', incremental_strategy='merge',
             on_schema_change='fail') }}
{% endif %}

select
    line_id, journal_id, transaction_date, fiscal_month,
    account_id, account_name, account_type, financial_statement,
    cost_center_id, currency_code, fx_rate_to_usd,
    debit_amount_usd, credit_amount_usd, signed_amount_usd,
    description, created_by, source_system,
    is_manual_entry, is_weekend_posting, is_round_dollar, is_orphan_account,
    _loaded_at
from {{ ref('int_journal_lines_cleaned') }}

{% if is_incremental() %}
  -- Open period only. Closed months are immutable, so they are never re-written.
  where fiscal_month >= (
      select date_trunc('month', max(transaction_date)) from {{ this }}
  )
{% endif %}
