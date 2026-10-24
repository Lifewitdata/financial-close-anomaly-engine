-- Gold: fact table for reporting. Incremental in Databricks prod (merge on line_id);
-- full table in DuckDB dev for reproducibility. Partition/cluster by fiscal_month in prod.
{{ config(materialized='table') }}
select
    line_id, journal_id, transaction_date, fiscal_month,
    account_id, account_name, account_type, financial_statement,
    cost_center_id, debit_amount_usd, credit_amount_usd, signed_amount_usd,
    currency_code, description, created_by, source_system,
    is_manual_entry, is_weekend_posting, is_round_dollar, is_orphan_account
from {{ ref('int_journal_lines_cleaned') }}
