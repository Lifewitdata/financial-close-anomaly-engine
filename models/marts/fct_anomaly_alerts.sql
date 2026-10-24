-- Gold: anomaly alerts for auditors. Rule-based + statistical (z-score by account).
with lines_raw as (select * from {{ ref('int_journal_lines_cleaned') }}),
lines as (
    select *,
           count(*) over (partition by transaction_date, account_id, abs(signed_amount_usd), description) as dup_count
    from lines_raw
),
rec as (select * from {{ ref('int_ledger_reconciliation') }}),
stats as (
    select account_id,
           avg(abs(signed_amount_usd)) as mean_abs_amount,
           stddev(abs(signed_amount_usd)) as std_abs_amount
    from lines_raw group by account_id
),
flagged as (
    select
        l.line_id, l.journal_id, l.transaction_date, l.account_id, l.account_name,
        l.signed_amount_usd, l.created_by, l.description,
        case
            when l.is_orphan_account then 'ORPHAN_ACCOUNT'
            when r.is_unbalanced then 'UNBALANCED_JOURNAL'
            when l.is_manual_entry and l.is_weekend_posting and l.is_round_dollar then 'MANUAL_WEEKEND_ROUND_DOLLAR'
            when l.dup_count > 1 then 'POSSIBLE_DUPLICATE'
            when s.std_abs_amount > 0 and abs(l.signed_amount_usd) > s.mean_abs_amount + 3 * s.std_abs_amount then 'AMOUNT_OUTLIER_3SIGMA'
            when l.is_manual_entry and abs(l.signed_amount_usd) >= 50000
                 and l.transaction_date = cast(date_trunc('month', l.transaction_date) + interval '1 month' - interval '1 day' as date) then 'PERIOD_END_MANUAL_LARGE'
            else null end as anomaly_type
    from lines l
    left join rec r on l.journal_id = r.journal_id
    left join stats s on l.account_id = s.account_id
)
select *,
    case anomaly_type
        when 'UNBALANCED_JOURNAL' then 'CRITICAL'
        when 'ORPHAN_ACCOUNT' then 'CRITICAL'
        when 'AMOUNT_OUTLIER_3SIGMA' then 'HIGH'
        when 'MANUAL_WEEKEND_ROUND_DOLLAR' then 'HIGH'
        when 'POSSIBLE_DUPLICATE' then 'HIGH'
        else 'MEDIUM' end as severity,
    'Review with controller; do not sign off close until cleared or waived with evidence.' as recommended_action
from flagged
where anomaly_type is not null
