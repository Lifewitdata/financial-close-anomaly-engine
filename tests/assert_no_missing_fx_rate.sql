-- SOX Control FC-05: every currency in the ledger must have a rate in seeds/fx_rates.csv.
-- Without this, convert_to_usd() silently falls back to 1.0 and every USD figure is wrong
-- by the missing rate - the one failure mode a finance pipeline must never hide.
-- Configured at default (error) severity on purpose: this is a pipeline bug, not a
-- journal exception for Finance to clear.
select distinct t.currency_code
from {{ ref('stg_erp__transactions') }} t
left join {{ ref('fx_rates') }} fx on fx.currency = t.currency_code
where fx.currency is null
