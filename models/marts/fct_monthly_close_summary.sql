-- Gold: the close checklist in data form. One row per fiscal month.
-- Close is READY only when: zero unbalanced journals, zero orphan lines,
-- and every journal reconciled. Finance signs off against this table (SOX control FC-01).
with rec as (select * from {{ ref('int_ledger_reconciliation') }}),
lines as (select * from {{ ref('int_journal_lines_cleaned') }})
select
    r.fiscal_month,
    count(distinct r.journal_id) as journal_count,
    sum(r.line_count) as line_count,
    round(sum(r.total_debits_usd),2) as total_debits_usd,
    round(sum(r.total_credits_usd),2) as total_credits_usd,
    sum(case when r.is_unbalanced then 1 else 0 end) as unbalanced_journal_count,
    sum(case when r.has_orphan_account then 1 else 0 end) as orphan_journal_count,
    (select count(*) from lines l where l.fiscal_month = r.fiscal_month and l.is_manual_entry) as manual_line_count,
    case when sum(case when r.is_unbalanced then 1 else 0 end) = 0
          and sum(case when r.has_orphan_account then 1 else 0 end) = 0
         then 'READY FOR SIGN-OFF' else 'BLOCKED - EXCEPTIONS OPEN' end as close_status
from rec r
group by r.fiscal_month
order by r.fiscal_month
