-- Bronze: 1:1 with source, only renaming / casting / trimming. No business logic here.
-- Audit rule: if Finance asks "what did ERP send us?", this model is the answer.
-- The erp() macro below resolves to the Delta Bronze source table on Databricks and to
-- the mirrored seed locally - the casting is identical either way.
with source as (
    select * from {{ erp('raw_transactions') }}
)
select
    cast(line_id as varchar)            as line_id,
    cast(journal_id as varchar)         as journal_id,
    cast(transaction_date as date)      as transaction_date,
    cast(account_id as varchar)         as account_id,
    cast(debit_amount as double)        as debit_amount,
    cast(credit_amount as double)       as credit_amount,
    upper(trim(cast(currency as varchar))) as currency_code,
    cast(cost_center_id as varchar)     as cost_center_id,
    trim(cast(description as varchar))  as description,
    lower(trim(cast(created_by as varchar))) as created_by,
    upper(trim(cast(source_system as varchar))) as source_system,
    current_timestamp                   as _loaded_at
from source
where line_id is not null
