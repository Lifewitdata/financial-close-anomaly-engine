-- Gold: the close checklist in data form. One row per fiscal month.
-- Close is READY only when: zero unbalanced journals and zero orphan lines.
-- Finance signs off against this table (SOX control FC-01 / FC-02 evidence).
-- Aggregates are cast to bigint: without the cast DuckDB widens sum() over count()
-- to HUGEINT and pandas renders "journal_count 39.0" on the dashboard.
with rec as (select * from {{ ref('int_ledger_reconciliation') }}),
manual as (
    select fiscal_month, count(*) as manual_line_count
    from {{ ref('int_journal_lines_cleaned') }}
    where is_manual_entry
    group by fiscal_month
)
select
    rec.fiscal_month,
    cast(count(distinct rec.journal_id) as bigint)                        as journal_count,
    cast(coalesce(sum(rec.line_count), 0) as bigint)                       as line_count,
    round(sum(rec.total_debits_usd), 2)                                    as total_debits_usd,
    round(sum(rec.total_credits_usd), 2)                                   as total_credits_usd,
    cast(sum(case when rec.is_unbalanced then 1 else 0 end) as bigint)      as unbalanced_journal_count,
    cast(sum(case when rec.has_orphan_account then 1 else 0 end) as bigint) as orphan_journal_count,
    cast(coalesce(max(manual.manual_line_count), 0) as bigint)              as manual_line_count,
    case when sum(case when rec.is_unbalanced then 1 else 0 end) = 0
          and sum(case when rec.has_orphan_account then 1 else 0 end) = 0
         then 'READY FOR SIGN-OFF' else 'BLOCKED - EXCEPTIONS OPEN' end     as close_status
from rec
left join manual on manual.fiscal_month = rec.fiscal_month
group by rec.fiscal_month
order by rec.fiscal_month
