"""
Dashboard tests using Streamlit's own AppTest harness (no browser, no port).

These are launch verification, not screenshot decoration: the app is actually executed,
its exceptions are asserted empty, and the KPIs are asserted to equal the same figures
scripts/verify_close.py locks against the warehouse.

Needs `dbt build --profiles-dir .` to have been run (the Gold tables come from the
DuckDB file). The fallback branch is tested separately against a warehouse file that
does not exist.
"""
import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
APP = str(ROOT / "app.py")


def _run(env_db=None):
    if env_db is None:
        os.environ.pop("FINANCIAL_CLOSE_DUCKDB", None)
    else:
        os.environ["FINANCIAL_CLOSE_DUCKDB"] = env_db
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_gold_layer_renders_without_exception():
    db = str(ROOT / "financial_close.duckdb")
    assert Path(db).exists(), "run `dbt build --profiles-dir .` before pytest"
    at = _run(db)
    assert not at.exception
    assert at.title[0].value == "Automated Financial Close & Anomaly Detection Engine"


def test_default_period_kpis_match_the_warehouse():
    at = _run(str(ROOT / "financial_close.duckdb"))
    values = {m.label: m.value for m in at.metric}
    assert values["Journals"] == "51"
    assert values["Unbalanced journals"] == "1"
    assert values["Lines on orphan accounts"] == "1"
    assert values["Manual lines"] == "10"
    assert values["Debits vs credits"] == "$1,759,359.41"
    assert any("BLOCKED - EXCEPTIONS OPEN" in md.value for md in at.markdown)


def test_alert_table_shows_the_severity_filter():
    at = _run(str(ROOT / "financial_close.duckdb"))
    assert [m.label for m in at.multiselect] == ["Severity"]
    assert len(at.dataframe) >= 2


def test_ledger_break_caption_is_the_planted_5000():
    at = _run(str(ROOT / "financial_close.duckdb"))
    captions = " ".join(c.value for c in at.caption)
    assert "$5,000.00" in captions


def test_scd2_history_section_is_present():
    at = _run(str(ROOT / "financial_close.duckdb"))
    assert any("SCD Type 2" in h.value for h in at.subheader)
    # Regression guard: the as-of comparison silently returned 0 rows when the
    # snapshot timestamps were compared against a datetime.date instead of a Timestamp.
    scd2 = at.dataframe[-1]
    assert len(scd2.value) == 5
    assert scd2.value["dbt_valid_to"].isna().all()


def test_missing_warehouse_asks_for_the_build_instead_of_inventing_numbers():
    at = _run(str(ROOT / "does_not_exist.duckdb"))
    assert not at.exception
    assert any("dbt build" in w.value for w in at.warning)
    # Only the raw feed may be shown: no control result can be computed off the CSV.
    labels = [m.label for m in at.metric]
    assert "Unbalanced journals" not in labels
    assert "Close period" not in [s.label for s in at.selectbox]
