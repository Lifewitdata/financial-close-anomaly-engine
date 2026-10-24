-- Silver: enriched journal lines. Business logic lives here, once.
-- - FX conversion to USD through the convert_to_usd() macro + the versioned fx_rates seed
-- - Signed amount: debits positive, credits negative (trial-balance convention)
-- - Fiscal period, weekend flag, manual-entry flag, round-dollar flag (anomaly signals)
-- - Orphan flag instead of dropping rows: auditors must SEE bad rows, not lose them.
with tx as (select * from {{ ref('stg_erp__transactions') }}),
accounts as (select * from {{ ref('stg_erp__accounts') }})
select
    t.line_id,
    t.journal_id,
    t.transaction_date,
    date_trunc('month', t.transaction_date) as fiscal_month,
    t.account_id,
    a.account_name,
    a.account_type,
    a.financial_statement,
    (a.account_id is null) as is_orphan_account,
    t.cost_center_id,
    t.debit_amount,
    t.credit_amount,
    t.currency_code,
    {{ fx_rate() }} as fx_rate_to_usd,
    {{ convert_to_usd('t.debit_amount') }} as debit_amount_usd,
    {{ convert_to_usd('t.credit_amount') }} as credit_amount_usd,
    round((t.debit_amount - t.credit_amount) * {{ fx_rate() }}, 2) as signed_amount_usd,
    t.description,
    t.created_by,
    t.source_system,
    (t.source_system = 'MANUAL') as is_manual_entry,
    (extract(dow from t.transaction_date) in (0,6)) as is_weekend_posting,
    (t.debit_amount + t.credit_amount >= 1000
     and cast(t.debit_amount + t.credit_amount as bigint) % 1000 = 0) as is_round_dollar,
    t._loaded_at
from tx t
left join accounts a on t.account_id = a.account_id
left join {{ ref('fx_rates') }} fx on fx.currency = t.currency_code
