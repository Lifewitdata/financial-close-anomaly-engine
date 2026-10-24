"""
Great Expectations - shift-left data quality gate for the ERP journal feed.

Validates the extract BEFORE dbt builds anything on top of it. In prod this runs as a GX
checkpoint against the Delta Bronze table; locally it runs against seeds/raw_transactions.csv,
which is the same grain and the same columns, so the gate is the same either way.

Two suites, deliberately separated, because they mean different things:

  1. STRUCTURAL GATE - shape of the feed (keys present, dates parse, no negatives,
     currencies known). Every expectation here MUST pass. If one fails the feed is
     malformed and the build should stop, so the script exits non-zero.

  2. BUSINESS CONTROLS - SOX rules evaluated at the source (FC-01 balance, FC-02 chart
     of accounts, duplicate lines). These are EXPECTED to fail on the planted anomalies,
     and the expected exception counts are pinned below. If a control stops firing, that
     is a regression in the rule, and the script exits non-zero.

Run:  python expectations/gx_suite_journal_lines.py
Proof: writes reports/gx_validation.json
"""
import hashlib
import json
import os
import sys
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

import great_expectations as gx  # noqa: E402  (must follow the warnings filter)
import great_expectations.expectations as gxe  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(BASE, "seeds", "raw_transactions.csv")
COA = os.path.join(BASE, "seeds", "raw_accounts.csv")
FX = os.path.join(BASE, "seeds", "fx_rates.csv")
OUT = os.path.join(BASE, "reports", "gx_validation.json")

CLOSE_WINDOW = ("2026-01-01", "2026-03-31")
KNOWN_CURRENCIES = ["USD", "EUR", "GBP", "INR"]

# Planted exceptions. Changing the sample data means changing these, deliberately.
EXPECTED_CONTROL_EXCEPTIONS = {
    "FC-02 orphan account_id not in chart of accounts": 1,      # JE9002-1, account 9999
    "FC-01 journal imbalance outside +/- 0.01": 1,              # JE9001, +5,000.00
    "Duplicate journal line on the same date": 4,               # JE9003/JE9004, both sides
}


def sha256(path):
    # Normalise CRLF before hashing: git's autocrlf can hand a Windows clone different
    # bytes for the same content, and a fingerprint that changes with the operating
    # system is not evidence of anything.
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def load_feed():
    # account_id arrives as a string on purpose: dbt casts it to varchar in Bronze, so the
    # gate must compare like-for-like against the chart of accounts. Left as int64, pandas
    # silently matches nothing and every "in set" expectation fails for the wrong reason.
    lines = pd.read_csv(FEED, dtype={"account_id": str, "line_id": str, "journal_id": str})
    lines["transaction_date"] = pd.to_datetime(lines["transaction_date"])

    # Same USD projection Silver uses, so the duplicate control here partitions on exactly
    # what fct_anomaly_alerts partitions on. Comparing local-currency amounts instead would
    # call two different-currency lines with equal face value "duplicates" - 24 false
    # exceptions on this feed against the 4 the warehouse reports.
    fx = pd.read_csv(FX)
    lines["fx_rate_to_usd"] = (
        lines["currency"].map(dict(zip(fx["currency"], fx["rate_to_usd"]))).fillna(1.0)
    )
    lines["abs_signed_amount_usd"] = (
        (lines["debit_amount"] - lines["credit_amount"]).mul(lines["fx_rate_to_usd"]).round(2).abs()
    )
    return lines


def journal_totals(lines):
    agg = (
        lines.groupby("journal_id", as_index=False)[["debit_amount", "credit_amount"]]
        .sum()
        .assign(imbalance=lambda d: (d.debit_amount - d.credit_amount).round(2))
    )
    return agg


def _jsonable(value):
    if isinstance(value, (int, float, bool, str, list, dict)) or value is None:
        return value
    return str(value)  # pd.Timestamp and friends inside expectation kwargs


def run_suite(context, name, frame, expectations):
    asset = context.data_sources.add_pandas(name).add_dataframe_asset(name)
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_feed")
    suite = context.suites.add(gx.ExpectationSuite(name=name))
    for expectation in expectations:
        suite.add_expectation(expectation)
    batch = batch_definition.get_batch(batch_parameters={"dataframe": frame})
    result = context.get_validator(batch=batch, expectation_suite=suite).validate()
    rows = []
    for entry in result["results"]:
        kwargs = {k: _jsonable(v) for k, v in dict(entry.expectation_config.kwargs).items()}
        kwargs.pop("batch_id", None)
        rows.append(
            {
                "expectation": entry.expectation_config.type,
                "on": kwargs,
                "success": bool(entry.success),
                "rows_evaluated": int(entry.result.get("element_count") or 0),
                "unexpected_rows": int(entry.result.get("unexpected_count") or 0),
            }
        )
    return rows, bool(result.success), dict(result.statistics)


def main():
    lines = load_feed()
    accounts = pd.read_csv(COA, dtype={"account_id": str})
    valid_accounts = sorted(accounts["account_id"].tolist())
    context = gx.get_context(mode="ephemeral")

    gate_rows, gate_success, gate_stats = run_suite(
        context,
        "journal_lines_structural_gate",
        lines,
        [
            gxe.ExpectTableRowCountToBeBetween(min_value=1, max_value=1_000_000),
            gxe.ExpectColumnValuesToNotBeNull(column="line_id"),
            gxe.ExpectColumnValuesToBeUnique(column="line_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="journal_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="transaction_date"),
            gxe.ExpectColumnValuesToBeBetween(
                column="transaction_date",
                min_value=pd.Timestamp(CLOSE_WINDOW[0]),
                max_value=pd.Timestamp(CLOSE_WINDOW[1]),
            ),
            gxe.ExpectColumnValuesToNotBeNull(column="debit_amount"),
            gxe.ExpectColumnValuesToNotBeNull(column="credit_amount"),
            gxe.ExpectColumnValuesToBeBetween(column="debit_amount", min_value=0),
            gxe.ExpectColumnValuesToBeBetween(column="credit_amount", min_value=0),
            gxe.ExpectColumnValuesToBeInSet(column="currency", value_set=KNOWN_CURRENCIES),
            gxe.ExpectColumnValuesToNotBeNull(column="cost_center_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="source_system"),
        ],
    )

    control_rows, _, _ = run_suite(
        context,
        "journal_lines_business_controls",
        lines,
        [
            gxe.ExpectColumnValuesToBeInSet(column="account_id", value_set=valid_accounts),
            gxe.ExpectCompoundColumnsToBeUnique(
                column_list=[
                    "transaction_date",
                    "account_id",
                    "abs_signed_amount_usd",
                    "description",
                ],
                ignore_row_if="any_value_is_missing",
            ),
        ],
    )
    balance_rows, _, _ = run_suite(
        context,
        "journal_debit_credit_balance",
        journal_totals(lines),
        [gxe.ExpectColumnValuesToBeBetween(column="imbalance", min_value=-0.01, max_value=0.01)],
    )
    control_rows = control_rows + balance_rows

    observed = {
        "FC-02 orphan account_id not in chart of accounts": control_rows[0]["unexpected_rows"],
        "Duplicate journal line on the same date": control_rows[1]["unexpected_rows"],
        "FC-01 journal imbalance outside +/- 0.01": control_rows[2]["unexpected_rows"],
    }
    findings_drift = {
        rule: {"expected": EXPECTED_CONTROL_EXCEPTIONS[rule], "observed": observed[rule]}
        for rule in EXPECTED_CONTROL_EXCEPTIONS
        if EXPECTED_CONTROL_EXCEPTIONS[rule] != observed[rule]
    }

    report = {
        "feed_file": os.path.relpath(FEED, BASE).replace("\\", "/"),
        "feed_sha256": sha256(FEED),
        "chart_of_accounts_sha256": sha256(COA),
        "fx_rates_sha256": sha256(FX),
        "rows_in_feed": int(len(lines)),
        "journals_in_feed": int(lines["journal_id"].nunique()),
        "close_window": list(CLOSE_WINDOW),
        "structural_gate": {
            "suite": "journal_lines_structural_gate",
            "success": gate_success,
            "statistics": gate_stats,
            "expectations": gate_rows,
        },
        "business_controls": {
            "note": "Failures here are the planted anomalies firing, which is the demo.",
            "exceptions_found": observed,
            "expected_exceptions": EXPECTED_CONTROL_EXCEPTIONS,
            "drift": findings_drift,
            "expectations": control_rows,
        },
        "verdict": "PASS" if (gate_success and not findings_drift) else "FAIL",
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"GREAT EXPECTATIONS - {report['verdict']}")
    print(f"  feed            : {report['feed_file']} ({report['rows_in_feed']} rows, "
          f"{report['journals_in_feed']} journals)")
    print(f"  structural gate : {gate_stats['successful_expectations']}/"
          f"{gate_stats['evaluated_expectations']} expectations passed"
          f"  [{'OK' if gate_success else 'MALFORMED FEED'}]")
    print(f"  business controls (expected to fire on the planted anomalies):")
    for row in control_rows:
        flag = "FIRED" if not row["success"] else "clean"
        print(f"    {flag:6s} {row['expectation']:42s} unexpected_rows={row['unexpected_rows']}")
    if findings_drift:
        print(f"  CONTROL DRIFT - rule stopped catching the planted exception: {findings_drift}")
    print(f"  wrote           : {os.path.relpath(OUT, BASE).replace(chr(92), '/')}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
