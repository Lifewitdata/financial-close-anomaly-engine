-- SOX Control FC-01: every journal must balance to the cent.
-- Configured as warn: in a real close this returns rows DURING the close so Finance
-- can fix them; the close summary blocks sign-off while any row exists.
{{ config(severity='warn') }}
select journal_id, total_debits_usd, total_credits_usd, imbalance_usd
from {{ ref('int_ledger_reconciliation') }}
where is_unbalanced
