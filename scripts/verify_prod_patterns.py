"""
Execution proof for the two production patterns the repo used to only document:

  1. fct_general_ledger  -> incremental MERGE on line_id
  2. snp_cost_centers    -> SCD Type 2 history via dbt snapshot

Both are verified by driving dbt through the phases a real close goes through
(new open-period rows arriving, a correction to an open period, a cost centre being
moved between regions) and asserting on what the warehouse actually holds afterwards.

Nothing here writes to a tracked file: the project is copied to a temp directory, the
seed CSVs are edited there, and dbt runs against a throwaway DuckDB file. The committed
evidence is reports/incremental_merge_proof.json and reports/scd2_history.csv.

Run:  python scripts/verify_prod_patterns.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
IGNORE = shutil.ignore_patterns(
    ".venv", "target", "logs", "dbt_packages", "*.duckdb", "reports", "__pycache__", ".git"
)
DBT = [sys.executable, "-m", "dbt.cli.main"]

NEW_BATCH = [
    "JE9100,JE9100-1,2026-04-15,5020,1000,0,USD,CC300,April retargeting invoice,system_erp,ERP",
    "JE9100,JE9100-2,2026-04-15,2000,0,1000,USD,CC300,April retargeting invoice,system_erp,ERP",
]


def run_dbt(project_dir, db_path, *args):
    env = dict(os.environ, FINANCIAL_CLOSE_DUCKDB=str(db_path))
    proc = subprocess.run(
        DBT + list(args) + ["--profiles-dir", str(project_dir), "--no-use-colors"],
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"dbt {' '.join(args)} failed\n{tail[-3000:]}")
    return tail


def query(db_path, sql):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def gl_state(db_path):
    rows = query(
        db_path,
        """
        select cast(fiscal_month as varchar) as fiscal_month,
               count(*) as rows,
               max(_loaded_at) as last_loaded
        from main_gold.fct_general_ledger
        group by 1 order by 1
        """,
    )
    total = query(db_path, "select count(*), count(distinct line_id) from main_gold.fct_general_ledger")[0]
    return {
        "row_count": int(total[0]),
        "distinct_line_ids": int(total[1]),
        "by_month": [
            {"fiscal_month": m, "rows": int(n), "max_loaded_at": str(ts)} for m, n, ts in rows
        ],
    }


def seed_lines(project_dir):
    return (project_dir / "seeds" / "raw_transactions.csv").read_text(encoding="utf-8")


def write_seed(project_dir, text):
    (project_dir / "seeds" / "raw_transactions.csv").write_text(text, encoding="utf-8", newline="\n")


def verify_incremental(project_dir, db_path):
    phases = []

    run_dbt(project_dir, db_path, "build")
    base = gl_state(db_path)
    phases.append({"phase": "1_initial_load", "expected": "full refresh of 264 lines", **base})

    run_dbt(project_dir, db_path, "run", "--select", "fct_general_ledger")
    rerun = gl_state(db_path)
    phases.append({"phase": "2_rerun_no_new_data", "expected": "open period re-merged only", **rerun})

    # Closed months must be byte-identical to the first load; only the open month moves.
    frozen = {m["fiscal_month"]: m["max_loaded_at"] for m in base["by_month"][:-1]}
    still_frozen = {
        m["fiscal_month"]: m["max_loaded_at"] == frozen[m["fiscal_month"]]
        for m in rerun["by_month"][:-1]
    }
    open_remerged = (
        rerun["by_month"][-1]["max_loaded_at"] != base["by_month"][-1]["max_loaded_at"]
    )

    original_seed = seed_lines(project_dir)
    write_seed(project_dir, original_seed.rstrip("\n") + "\n" + "\n".join(NEW_BATCH) + "\n")
    run_dbt(project_dir, db_path, "seed")
    run_dbt(project_dir, db_path, "run", "--select", "fct_general_ledger")
    appended = gl_state(db_path)
    new_ids = query(
        db_path,
        "select count(*) from main_gold.fct_general_ledger where journal_id = 'JE9100'",
    )[0][0]
    april = query(
        db_path,
        "select sum(debit_amount_usd) from main_gold.fct_general_ledger where journal_id = 'JE9100'",
    )[0][0]
    phases.append(
        {
            "phase": "3_new_open_period_batch",
            "expected": "2 new lines inserted by the merge, older rows untouched",
            "new_journal_lines_present": int(new_ids),
            "april_debits_usd": float(april),
            **appended,
        }
    )

    # Restate one line inside the open period - the WHEN MATCHED UPDATE branch.
    corrected = seed_lines(project_dir).replace(
        "JE9100,JE9100-1,2026-04-15,5020,1000,0,USD",
        "JE9100,JE9100-1,2026-04-15,5020,1750,0,USD",
    )
    assert corrected != seed_lines(project_dir), "correction fixture did not apply"
    write_seed(project_dir, corrected)
    run_dbt(project_dir, db_path, "seed")
    run_dbt(project_dir, db_path, "run", "--select", "fct_general_ledger")
    corrected_state = gl_state(db_path)
    corrected_amount = query(
        db_path,
        "select debit_amount_usd from main_gold.fct_general_ledger where line_id = 'JE9100-1'",
    )[0][0]
    phases.append(
        {
            "phase": "4_correction_to_open_period",
            "expected": "same line_id updated in place, no duplicate row",
            "je9100_1_debit_amount_usd": float(corrected_amount),
            **corrected_state,
        }
    )

    write_seed(project_dir, original_seed)

    checks = {
        "initial_load_is_264_rows": base["row_count"] == 264,
        "rerun_does_not_duplicate_rows": rerun["row_count"] == base["row_count"],
        "closed_periods_not_rewritten": all(still_frozen.values()),
        "open_period_was_remerged": open_remerged,
        "merge_inserted_new_lines": appended["row_count"] == base["row_count"] + 2,
        "new_journal_lines_in_target": int(new_ids) == 2,
        "merge_updated_in_place_no_duplicate": corrected_state["row_count"]
        == appended["row_count"],
        "updated_amount_is_1750": float(corrected_amount) == 1750.0,
        "line_id_stays_unique_throughout": all(
            p["distinct_line_ids"] == p["row_count"] for p in (base, rerun, appended, corrected_state)
        ),
    }
    return {
        "model": "main_gold.fct_general_ledger",
        "materialization": "incremental (merge on line_id)",
        "closed_months_frozen_across_rerun": still_frozen,
        "phases": phases,
        "checks": checks,
        "passed": all(checks.values()),
    }


def verify_scd2(project_dir, db_path):
    history_sql = """
        select cost_center_id, cost_center_name, region,
               cast(dbt_valid_from as varchar) as dbt_valid_from,
               coalesce(cast(dbt_valid_to as varchar), 'ACTIVE') as dbt_valid_to
        from main_gold_scd.dim_cost_centers_history
        order by cost_center_id, dbt_valid_from
    """
    path = db_path.with_name("scd2.duckdb")
    run_dbt(project_dir, path, "build")
    phase1 = query(path, history_sql)
    counts1 = query(path, "select count(*), sum(case when dbt_valid_to is null then 1 else 0 end) from main_gold_scd.dim_cost_centers_history")[0]

    seed_path = project_dir / "seeds" / "raw_cost_centers.csv"
    original = seed_path.read_text(encoding="utf-8")
    # Two real org-chart events: Sales moves region, Marketing is renamed.
    moved = (
        original.replace("CC200,Sales,North America", "CC200,Sales,EMEA")
        .replace("CC300,Marketing,North America", "CC300,Demand Marketing,North America")
    )
    assert moved != original, "cost-centre fixture did not change"
    seed_path.write_text(moved, encoding="utf-8", newline="\n")

    # The snapshot reads the seeded table, not the CSV - reload the seed first or the
    # org change is invisible to dbt.
    run_dbt(project_dir, path, "seed", "--select", "raw_cost_centers")
    run_dbt(project_dir, path, "snapshot")
    phase2 = query(path, history_sql)
    counts2 = query(path, "select count(*), sum(case when dbt_valid_to is null then 1 else 0 end) from main_gold_scd.dim_cost_centers_history")[0]

    run_dbt(project_dir, path, "snapshot")
    counts3 = query(path, "select count(*), sum(case when dbt_valid_to is null then 1 else 0 end) from main_gold_scd.dim_cost_centers_history")[0]

    out_of_order = query(
        path,
        """
        select count(*) from (
          select cost_center_id from main_gold_scd.dim_cost_centers_history
          where dbt_valid_to is null group by 1 having count(*) > 1
        )
        """,
    )[0][0]

    with open(REPORTS / "scd2_history.csv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("cost_center_id,cost_center_name,region,dbt_valid_from,dbt_valid_to\n")
        for row in phase2:
            fh.write(",".join(str(c) for c in row) + "\n")

    checks = {
        "first_snapshot_seeds_current_state": int(counts1[0]) == 5 and int(counts1[1]) == 5,
        "change_creates_a_new_version": int(counts2[0]) == int(counts1[0]) + 2,
        "superseded_versions_are_closed": int(counts2[1]) == 5,
        "unchanged_reload_adds_no_history": counts3 == counts2,
        "exactly_one_active_version_per_key": int(out_of_order) == 0,
        "history_is_queryable_as_of_a_date": len(phase1) == 5 and len(phase2) == 7,
    }
    return {
        "snapshot": "main_gold_scd.dim_cost_centers_history",
        "strategy": "check (cost_center_name, region)",
        "rows_after_phase1_unchanged_feed": int(counts1[0]),
        "rows_after_phase2_org_change": int(counts2[0]),
        "active_versions_after_phase2": int(counts2[1]),
        "rows_after_phase3_identical_reload": int(counts3[0]),
        "history_rows": [list(r) for r in phase2],
        "checks": checks,
        "passed": all(checks.values()),
        "evidence_file": "reports/scd2_history.csv",
    }


def main():
    REPORTS.mkdir(exist_ok=True)
    results = {}
    for name, fn, key in (
        ("incremental", verify_incremental, "incremental_merge"),
        ("scd2", verify_scd2, "scd2_snapshot"),
    ):
        workdir = Path(tempfile.mkdtemp(prefix=f"fcae-{name}-"))
        try:
            project_dir = workdir / "project"
            shutil.copytree(ROOT, project_dir, ignore=IGNORE, symlinks=False)
            db_path = workdir / "warehouse.duckdb"
            results[key] = fn(project_dir, db_path)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    with open(REPORTS / "incremental_and_scd2_proof.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
        fh.write("\n")

    ok = True
    for key, block in results.items():
        print(f"{key}: {'PASS' if block['passed'] else 'FAIL'}")
        for check, passed in sorted(block["checks"].items()):
            print(f"  [{'x' if passed else ' '}] {check}")
            ok = ok and passed
    print(f"wrote reports/incremental_and_scd2_proof.json, reports/scd2_history.csv")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
