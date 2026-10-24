"""
Turn the evidence JSON in reports/ into the GitHub Actions job summary.

Prints markdown on stdout; the workflow redirects it into $GITHUB_STEP_SUMMARY. Missing
files are reported as "not reached" rather than crashing, because the step runs with
`if: always()` so a failed build still leaves a readable trail.

Usage: python scripts/ci_summary.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / "reports" / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def tally(label, checks):
    ok = sum(1 for v in checks.values() if v)
    print(f"- **{label}**: {ok}/{len(checks)} checks passed")


def main() -> int:
    close = load("close_metrics.json")
    prod = load("incremental_and_scd2_proof.json")
    gx = load("gx_validation.json")

    print("## Close pipeline\n")
    if close:
        m = close["metrics"]
        t = m["dbt_run"]["tally"]
        print(f"- **dbt build**: {sum(t.values())} nodes - {t.get('pass', 0)} tests passed, "
              f"{t.get('success', 0)} models built, {t.get('warn', 0)} control warnings "
              "(the planted anomalies firing, by design)")
        tally("verify_close", close["checks"])
        mar = m["monthly_close"]["2026-03"]
        print(f"- **2026-03 close**: {mar['journals']} journals, ${mar['debits']:,.2f} debits, "
              f"{mar['status']}")
        a = m["anomaly_alerts"]
        print(f"- **anomaly alerts**: {a['rows']} rows across {len(a['journals'])} planted "
              f"journals, {len(a['by_type'])} of the 6 rule types represented")
        print(f"- **lineage**: {m['lineage']['nodes']} nodes, {m['lineage']['edges']} edges")
    else:
        print("- **verify_close**: not reached")

    if gx:
        sg = gx["structural_gate"]
        print(f"- **Great Expectations**: {gx['verdict']} - {len(sg['expectations'])} "
              f"structural expectations, controls fired "
              + ", ".join(f"{k.split(' ', 1)[0]}={v}"
                          for k, v in sorted(gx["business_controls"]["exceptions_found"].items())))
    else:
        print("- **Great Expectations**: not reached")

    if prod:
        inc, scd = prod["incremental_merge"], prod["scd2_snapshot"]
        tally("Incremental merge on line_id", inc["checks"])
        tally("SCD Type 2 snapshot", scd["checks"])
        print(f"  - history rows: {scd['rows_after_phase1_unchanged_feed']} unchanged feed -> "
              f"{scd['rows_after_phase2_org_change']} after an org change -> "
              f"{scd['rows_after_phase3_identical_reload']} on an identical reload")
    else:
        print("- **prod patterns**: not reached")

    print("\nCommitted evidence: `reports/`, `LINEAGE.md`, `docs/dashboard.png`, "
          "`docs/dbt-lineage.png`. The full dbt docs bundle is in this run's artefacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
