# Automated Financial Close & Anomaly Detection Engine

**Stack:** dbt Core • Databricks / Delta Lake (prod) • DuckDB (local dev) • Great Expectations • Streamlit • PySpark

A finance-grade analytics engineering project that automates the month-end close:
every journal must balance, every account must exist in the chart of accounts, and
every unusual entry must be flagged *before* a controller signs off. Built to the
standard an IPO audit (SOX) would test: controls are code, evidence is a table,
and no Gold number can be edited by hand.

## Architecture (Medallion)

```
ERP extract (CSV / Delta Bronze)
  -> seeds: raw_transactions, raw_accounts, raw_cost_centers, fx_rates
  -> Bronze  models/staging/       stg_erp__transactions, stg_erp__accounts   (rename/cast only)
  -> Silver  models/intermediate/  int_journal_lines_cleaned (FX->USD, signed amount, anomaly flags)
                                   int_ledger_reconciliation (per-journal debit = credit check)
  -> Gold    models/marts/         fct_general_ledger, fct_monthly_close_summary,
                                   dim_cost_centers, fct_anomaly_alerts
  -> Streamlit app.py              close progress + anomaly alerts for Finance
```

**Databricks prod note:** `profiles.yml` ships a `databricks` target (Delta, catalog
`finance_catalog`). Locally everything runs on DuckDB with identical model SQL, so a
reviewer can reproduce the full build with no cloud account. PySpark/Delta is the
ingestion layer in prod (ERP files land in Delta Bronze, dbt models them from there);
dbt, not notebooks, owns all transformation logic so it is tested and versioned.

## Controls (IPO / SOX readiness)

| Control | Implementation | Evidence |
|---|---|---|
| FC-01 Debits = Credits | `tests/assert_debits_equals_credits.sql` + `int_ledger_reconciliation.is_unbalanced` | `fct_monthly_close_summary.unbalanced_journal_count` |
| FC-02 No orphan accounts | `tests/assert_no_orphan_accounts.sql` + `is_orphan_account` flag (rows retained, not dropped) | `fct_anomaly_alerts` where type = ORPHAN_ACCOUNT |
| FC-03 Schema / DQ gates | dbt generic tests (not_null, unique, accepted_values) + Great Expectations suite in `expectations/` | `dbt build` test report |
| FC-04 Anomaly review | Rule + statistical alerts with severity & owner action | `fct_anomaly_alerts` |

## Anomaly rules in `fct_anomaly_alerts`

1. **UNBALANCED_JOURNAL** (CRITICAL) - journal debits != credits
2. **ORPHAN_ACCOUNT** (CRITICAL) - account not in chart of accounts
3. **MANUAL_WEEKEND_ROUND_DOLLAR** (HIGH) - manual + weekend + round $1,000 multiple: classic fraud-risk pattern
4. **POSSIBLE_DUPLICATE** (HIGH) - same date + account + amount + description on >1 line
5. **AMOUNT_OUTLIER_3SIGMA** (HIGH) - amount > mean + 3σ for that account
6. **PERIOD_END_MANUAL_LARGE** (MEDIUM) - large manual entry on the last day of the period

## Sample data - planted anomalies (demo)

`data/raw_transactions.csv` = 264 journal lines, 132 journals, Jan-Mar 2026, USD/EUR/GBP.
Six anomalies are planted on purpose (JE9001-JE9007) so the controls visibly fire:
unbalanced JE9001 (+$5,000), orphan account 9999 (JE9002), duplicate pair JE9003/JE9004,
weekend round-dollar $50,000 (JE9005), $485,000 outlier (JE9006), $220,000 period-end
manual revenue entry (JE9007). All other journals balance.

## Run it

```bash
pip install -r requirements.txt
# dbt looks for profiles.yml in ~/.dbt by default:
cp profiles.yml ~/.dbt/profiles.yml   # or run with --profiles-dir .
dbt seed --profiles-dir .
dbt build --profiles-dir .           # seeds + models + tests
streamlit run app.py                 # close dashboard
python expectations/gx_suite_journal_lines.py
```

Databricks: set `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`
and run `dbt build --target databricks`.

## Design decisions a senior would make here

- **Bronze does no business logic** - auditors can trace any Gold number to source in 2 hops.
- **Orphan rows are flagged, never dropped** - dropping them would make debits "balance" by hiding the problem.
- **FX logic is one macro + one versioned seed table** - not copied into 5 models.
- **Audit tests are `severity: warn`** during the close (they list what Finance must fix)
  and the **close summary blocks sign-off** while exceptions exist - the control is the gate, not a failed pipeline.
- **Incremental in prod, full-refresh locally** - `fct_general_ledger` is built as a table;
  on Databricks switch it to incremental/merge on `line_id` and partition by `fiscal_month`.

## Honest limits

Synthetic data only (no real ledger). Databricks target is configured but not run live
in this repo - the verified build is the DuckDB mirror with identical model SQL.
Great Expectations suite is a runnable starter checkpoint, not a full GX Cloud setup.

## Verified build (local, DuckDB mirror)

```
dbt build  ->  PASS=36  WARN=2  ERROR=0  TOTAL=38
  WARN assert_debits_equals_credits  (1 journal: JE9001, imbalance $5,000)
  WARN assert_no_orphan_accounts     (1 line: JE9002-1, account 9999)
  -> warnings are the planted anomalies firing by design, not build failures.

fct_monthly_close_summary
  2026-01  39 journals   debits $1,024,196.02  READY FOR SIGN-OFF
  2026-02  42 journals   debits $1,008,814.02  READY FOR SIGN-OFF
  2026-03  51 journals   debits $1,759,359.41  BLOCKED - EXCEPTIONS OPEN

fct_anomaly_alerts: 14 alert lines across JE9001-JE9007, all 6 rule types fire.
fct_general_ledger: 264 rows (full reconciliation to source).
```
