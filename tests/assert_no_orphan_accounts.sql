-- SOX Control FC-02: every journal line must map to the chart of accounts.
{{ config(severity='warn') }}
select line_id, journal_id, account_id, transaction_date
from {{ ref('int_journal_lines_cleaned') }}
where is_orphan_account
