-- Silver: one row per journal. The reconciliation control for the close.
-- A journal is audit-ready only when debits = credits (to the cent) in USD.
with lines as (select * from {{ ref('int_journal_lines_cleaned') }})
select
    journal_id,
    min(transaction_date) as transaction_date,
    min(fiscal_month) as fiscal_month,
    count(*) as line_count,
    round(sum(debit_amount_usd),2) as total_debits_usd,
    round(sum(credit_amount_usd),2) as total_credits_usd,
    round(sum(debit_amount_usd) - sum(credit_amount_usd),2) as imbalance_usd,
    abs(round(sum(debit_amount_usd) - sum(credit_amount_usd),2)) > 0.01 as is_unbalanced,
    bool_or(is_orphan_account) as has_orphan_account,
    bool_or(is_manual_entry) as is_manual_journal,
    max(created_by) as created_by
from lines
group by journal_id
