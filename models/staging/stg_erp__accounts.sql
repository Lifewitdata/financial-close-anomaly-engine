-- Bronze: chart of accounts, cleaned only.
with source as (select * from {{ erp('raw_accounts') }})
select
    cast(account_id as varchar)         as account_id,
    trim(cast(account_name as varchar)) as account_name,
    trim(cast(account_type as varchar)) as account_type,
    trim(cast(financial_statement as varchar)) as financial_statement,
    current_timestamp as _loaded_at
from source
