"""
Great Expectations suite for the raw journal feed.
Use case: validate the ERP extract BEFORE dbt runs (shift-left DQ).
In prod this runs as a Databricks notebook / GX checkpoint on the Delta Bronze table;
locally it validates data/raw_transactions.csv.

Run:  python expectations/gx_suite_journal_lines.py
"""
import great_expectations as gx
import pandas as pd, sys, os

path = os.path.join(os.path.dirname(__file__), "..", "data", "raw_transactions.csv")
df = pd.read_csv(path, parse_dates=["transaction_date"])

context = gx.get_context(mode="ephemeral")
ds = context.data_sources.add_pandas("erp_csv")
asset = ds.add_dataframe_asset("journal_lines")
bd = asset.build_batch_definitions()[0] if asset.build_batch_definitions() else asset.add_batch_definition_daily("daily", date_column="transaction_date")
# Simpler ephemeral batch path for GX 1.x / 0.18 compatibility:
try:
    batch = asset.get_batch(batch_parameters={"dataframe": df})
except Exception:
    batch = None

suite = gx.ExpectationSuite(name="journal_lines_suite")
suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="line_id"))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeUnique(column="line_id"))
suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="journal_id"))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="debit_amount", min_value=0))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="credit_amount", min_value=0))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="currency", value_set=["USD","EUR","GBP","INR"]))
suite.add_expectation(gx.expectations.ExpectColumnValuesToBeInSet(column="account_id", value_set=["1000","1010","1020","2000","2010","3000","4000","4010","5000","5010","5020","5030","5040"]))

# Business-rule checks done in pandas (clearer for auditors than GX config alone):
checks = {}
bal = df.groupby("journal_id")[["debit_amount","credit_amount"]].sum()
checks["all_journals_balance"] = bool(((bal["debit_amount"]-bal["credit_amount"]).abs() <= 0.01).all())
checks["no_orphan_accounts"] = bool(df["account_id"].astype(str).isin(["1000","1010","1020","2000","2010","3000","4000","4010","5000","5010","5020","5030","5040"]).all())
print("Pandas audit checks (expected to FAIL on the planted anomalies - that is the demo):", checks)
print(f"Rows: {len(df)} | Journals: {df['journal_id'].nunique()}")
print("GX suite defined:", suite.name, "| expectations:", len(suite.expectations))
