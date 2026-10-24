{{ config(severity='warn') }}
select line_id, journal_id, debit_amount, credit_amount
from {{ ref('stg_erp__transactions') }}
where debit_amount < 0 or credit_amount < 0
