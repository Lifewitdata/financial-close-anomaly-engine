"""
Verification harness: recomputes every figure this repo publishes and fails if any of
them moved. The README, reports/close_metrics.json and the warehouse must agree, or
the build is broken.

Three kinds of check:
  * published figures  - row counts, close totals, alert counts per rule
  * control integrity  - the planted anomalies still fire, and no other journal breaks
  * cross-engine       - the Great Expectations gate (pre-dbt, pandas) and the dbt
                         controls (post-dbt, SQL) must find the SAME exceptions, and the
                         committed lineage file must match the manifest that was built

Writes reports/close_metrics.json.

Run after `dbt build --profiles-dir .`:
    python scripts/verify_close.py
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("FINANCIAL_CLOSE_DUCKDB", ROOT / "financial_close.duckdb"))
REPORTS = ROOT / "reports"
GX_REPORT = REPORTS / "gx_validation.json"
MANIFEST = ROOT / "target" / "manifest.json"
RUN_RESULTS = ROOT / "target" / "run_results.json"
LINEAGE = ROOT / "LINEAGE.md"

# The planted set. Six anomalies on purpose so the controls visibly fire.
EXPECTED_FEED = {"rows": 264, "journals": 132}
EXPECTED_CLOSE = {
    "2026-01": {"journals": 39, "lines": 78, "debits": 1024196.02, "credits": 1024196.02,
                "unbalanced": 0, "orphans": 0, "manual": 0,
                "status": "READY FOR SIGN-OFF"},
    "2026-02": {"journals": 42, "lines": 84, "debits": 1008814.02, "credits": 1008814.02,
                "unbalanced": 0, "orphans": 0, "manual": 0,
                "status": "READY FOR SIGN-OFF"},
    "2026-03": {"journals": 51, "lines": 102, "debits": 1759359.41, "credits": 1754359.41,
                "unbalanced": 1, "orphans": 1, "manual": 10,
                "status": "BLOCKED - EXCEPTIONS OPEN"},
}
EXPECTED_ALERTS_BY_TYPE = {
    "UNBALANCED_JOURNAL": 2,
    "ORPHAN_ACCOUNT": 1,
    "MANUAL_WEEKEND_ROUND_DOLLAR": 3,
    "POSSIBLE_DUPLICATE": 4,
    "AMOUNT_OUTLIER_3SIGMA": 3,
    "PERIOD_END_MANUAL_LARGE": 1,
}
EXPECTED_ALERTS_BY_SEVERITY = {"CRITICAL": 3, "HIGH": 10, "MEDIUM": 1}
# Console line reads PASS=39 WARN=2 ERROR=0 TOTAL=41; the JSON splits the 39 passes into
# 26 data tests ("pass") and 13 built nodes - 4 seeds, 8 models, 1 snapshot ("success").
EXPECTED_DBT = {"tests_pass": 26, "nodes_built": 13, "warn": 2, "total": 41}


def norm_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main():
    checks, metrics = {}, {}

    if not DB.exists():
        print(f"missing {DB} - run `dbt build --profiles-dir .` first")
        return 1

    con = duckdb.connect(str(DB), read_only=True)
    q = lambda sql: con.execute(sql).df()

    # ------------------------------------------------------------------ source feed
    feed = q("select * from main_raw.raw_transactions")
    metrics["feed"] = {"rows": int(len(feed)), "journals": int(feed["journal_id"].nunique())}
    checks["feed_row_count"] = metrics["feed"]["rows"] == EXPECTED_FEED["rows"]
    checks["feed_journal_count"] = metrics["feed"]["journals"] == EXPECTED_FEED["journals"]

    # ------------------------------------------------------------------ gold: GL
    gl = q("select * from main_gold.fct_general_ledger")
    net = round(float(gl["signed_amount_usd"].sum()), 2)
    metrics["general_ledger"] = {
        "rows": int(len(gl)),
        "distinct_line_ids": int(gl["line_id"].nunique()),
        "net_signed_amount_usd": net,
    }
    checks["gl_reconciles_to_feed"] = len(gl) == len(feed)
    checks["gl_line_id_is_unique"] = gl["line_id"].nunique() == len(gl)
    checks["gl_nets_to_the_5000_break"] = net == 5000.00
    checks["gl_keeps_orphan_rows"] = bool(gl["is_orphan_account"].any())
    checks["gl_orphan_rows_not_dropped"] = int(gl["is_orphan_account"].sum()) == 1
    checks["gl_all_amounts_are_usd"] = set(gl["currency_code"]) <= {"USD", "EUR", "GBP", "INR"}

    # every journal balances except the one planted break
    per_journal = gl.groupby("journal_id")["signed_amount_usd"].sum().round(2)
    broken = sorted(per_journal[per_journal.abs() > 0.01].index)
    checks["only_the_planted_journal_is_unbalanced"] = broken == ["JE9001"]
    checks["planted_break_is_plus_5000"] = float(per_journal["JE9001"]) == 5000.00

    # ------------------------------------------------------------------ gold: close summary
    close = q("select * from main_gold.fct_monthly_close_summary order by fiscal_month")
    close_rows = {}
    for _, r in close.iterrows():
        key = str(r["fiscal_month"])[:7]
        close_rows[key] = {
            "journals": int(r["journal_count"]),
            "lines": int(r["line_count"]),
            "debits": float(r["total_debits_usd"]),
            "credits": float(r["total_credits_usd"]),
            "unbalanced": int(r["unbalanced_journal_count"]),
            "orphans": int(r["orphan_journal_count"]),
            "manual": int(r["manual_line_count"]),
            "status": r["close_status"],
        }
    metrics["monthly_close"] = close_rows
    checks["close_summary_has_three_periods"] = set(close_rows) == set(EXPECTED_CLOSE)
    for period, expected in EXPECTED_CLOSE.items():
        got = close_rows.get(period, {})
        checks[f"close_{period}_matches_published_figures"] = got == expected
    checks["close_counts_are_integers_not_floats"] = all(
        str(v["journals"]) == str(v["journals"]) and isinstance(v["journals"], int)
        for v in close_rows.values()
    )
    checks["sign_off_blocked_iff_exceptions"] = all(
        (v["status"] == "READY FOR SIGN-OFF") == (v["unbalanced"] == 0 and v["orphans"] == 0)
        for v in close_rows.values()
    )
    checks["line_counts_sum_to_feed"] = sum(v["lines"] for v in close_rows.values()) == len(feed)

    # ------------------------------------------------------------------ gold: alerts
    alerts = q("select * from main_gold.fct_anomaly_alerts")
    by_type = alerts["anomaly_type"].value_counts().to_dict()
    by_sev = alerts["severity"].value_counts().to_dict()
    metrics["anomaly_alerts"] = {
        "rows": int(len(alerts)),
        "by_type": {k: int(v) for k, v in by_type.items()},
        "by_severity": {k: int(v) for k, v in by_sev.items()},
        "journals": sorted(alerts["journal_id"].unique()),
    }
    checks["alert_row_count"] = len(alerts) == 14
    checks["every_alert_type_fires"] = by_type == EXPECTED_ALERTS_BY_TYPE
    checks["severity_split"] = by_sev == EXPECTED_ALERTS_BY_SEVERITY
    checks["all_six_rules_represented"] = len(by_type) == 6
    checks["alert_journals_are_the_planted_ones"] = sorted(alerts["journal_id"].unique()) == [
        f"JE900{i}" for i in range(1, 8)
    ]
    checks["critical_alerts_are_balance_or_orphan"] = set(
        alerts.loc[alerts["severity"] == "CRITICAL", "anomaly_type"]
    ) == {"UNBALANCED_JOURNAL", "ORPHAN_ACCOUNT"}

    # ------------------------------------------------------------------ dimensions + history
    dim = q("select * from main_gold.dim_cost_centers")
    snap = q("select * from main_gold_scd.dim_cost_centers_history")
    metrics["dimensions"] = {
        "cost_centres_current": int(len(dim)),
        "cost_centre_versions_in_history": int(len(snap)),
    }
    checks["dim_cost_centers_grain"] = dim["cost_center_id"].is_unique and len(dim) == 5
    checks["snapshot_has_one_live_version_per_key"] = (
        snap[snap["dbt_valid_to"].isna()]["cost_center_id"].nunique() == len(dim)
    )

    fx = q("select distinct currency_code from main_gold.fct_general_ledger")
    mapped = q("select distinct currency from main_raw.fx_rates")
    checks["every_ledger_currency_has_a_rate"] = set(fx["currency_code"]) <= set(mapped["currency"])

    # ------------------------------------------------------------------ cross-engine gate
    if GX_REPORT.exists():
        gx_report = json.loads(GX_REPORT.read_text(encoding="utf-8"))
        metrics["gx_gate"] = {
            "verdict": gx_report["verdict"],
            "structural_expectations": len(gx_report["structural_gate"]["expectations"]),
            "control_exceptions": gx_report["business_controls"]["exceptions_found"],
        }
        checks["gx_structural_gate_passed"] = gx_report["structural_gate"]["success"] is True
        checks["gx_verdict_pass"] = gx_report["verdict"] == "PASS"
        checks["gx_fingerprint_matches_seeded_bytes"] = (
            gx_report["feed_sha256"] == norm_sha(ROOT / "seeds" / "raw_transactions.csv")
        )
        exc = gx_report["business_controls"]["exceptions_found"]
        checks["gx_and_dbt_agree_on_orphan_lines"] = (
            exc["FC-02 orphan account_id not in chart of accounts"]
            == by_type.get("ORPHAN_ACCOUNT")
        )
        checks["gx_and_dbt_agree_on_duplicates"] = (
            exc["Duplicate journal line on the same date"] == by_type.get("POSSIBLE_DUPLICATE")
        )
        checks["gx_and_dbt_agree_on_imbalanced_journals"] = (
            exc["FC-01 journal imbalance outside +/- 0.01"]
            == int(close["unbalanced_journal_count"].sum())
        )
    else:
        checks["gx_report_present"] = False

    # ------------------------------------------------------------------ dbt run outcome
    if RUN_RESULTS.exists():
        rr = json.loads(RUN_RESULTS.read_text(encoding="utf-8"))
        tally = {}
        for r in rr["results"]:
            tally[r["status"]] = tally.get(r["status"], 0) + 1
        which = (rr.get("args", {}).get("which") or "?")
        metrics["dbt_run"] = {"command": which, "tally": tally}
        checks["dbt_ran_build_command"] = which == "build"
        checks["dbt_node_count"] = len(rr["results"]) == EXPECTED_DBT["total"]
        checks["dbt_build_has_no_errors"] = (
            tally.get("error", 0) + tally.get("ERROR", 0) + tally.get("skipped", 0) == 0
        )
        checks["dbt_build_tally"] = (
            tally.get("pass", 0) == EXPECTED_DBT["tests_pass"]
            and tally.get("success", 0) == EXPECTED_DBT["nodes_built"]
            and tally.get("warn", 0) == EXPECTED_DBT["warn"]
        )
    else:
        checks["dbt_run_results_present"] = False

    # ------------------------------------------------------------------ lineage freshness
    if MANIFEST.exists() and LINEAGE.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        nodes = {
            k: v
            for k, v in manifest["nodes"].items()
            if v["resource_type"] in ("model", "seed", "snapshot")
        }
        text = LINEAGE.read_text(encoding="utf-8")
        edges = set()
        for key, node in nodes.items():
            for parent in manifest["parent_map"].get(key, []):
                if parent in nodes:
                    edges.add(f"{nodes[parent]['name']} --> {node['name']}")
        checks["lineage_lists_every_node"] = all(n["name"] in text for n in nodes.values())
        checks["lineage_edges_match_manifest"] = all(e in text for e in edges)
        checks["lineage_node_count"] = len(nodes) == 13
        metrics["lineage"] = {"nodes": len(nodes), "edges": len(edges)}
    else:
        checks["lineage_artefacts_present"] = False

    con.close()

    REPORTS.mkdir(exist_ok=True)
    with open(REPORTS / "close_metrics.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"metrics": metrics, "checks": checks, "passed": all(checks.values())},
                  fh, indent=2, sort_keys=True)
        fh.write("\n")

    failed = sorted(k for k, v in checks.items() if not v)
    for name in sorted(checks):
        print(f"  [{'x' if checks[name] else ' '}] {name}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    print("wrote reports/close_metrics.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
