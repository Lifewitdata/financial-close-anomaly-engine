"""
Streamlit - month-end close monitoring dashboard.

Every number on this page comes from a dbt-built Gold table. The dashboard deliberately
does not re-implement a control in Python: a dashboard that quietly disagrees with the
warehouse is worse than one that tells you to run the pipeline. So when the Gold tables
are not built yet, the app says so and shows the raw feed instead of approximating.

Run after `dbt build --profiles-dir .`:
    streamlit run app.py
"""
import os

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Financial Close & Anomaly Detection", layout="wide")

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("FINANCIAL_CLOSE_DUCKDB", os.path.join(BASE, "financial_close.duckdb"))
GOLD = "main_gold"
SCD = "main_gold_scd"

st.title("Automated Financial Close & Anomaly Detection Engine")
st.caption(
    "dbt + Databricks (prod) / DuckDB (local)  |  Bronze -> Silver -> Gold  |  "
    "SOX controls FC-01 to FC-05"
)


def read(sql):
    """Run a query against the built warehouse; return None if that object is not there."""
    import duckdb

    if not os.path.exists(DB):
        return None
    con = duckdb.connect(DB, read_only=True)
    try:
        return con.execute(sql).df()
    except Exception:
        return None
    finally:
        con.close()


gl = read(f"select * from {GOLD}.fct_general_ledger")
close = read(f"select * from {GOLD}.fct_monthly_close_summary order by fiscal_month")
alerts = read(f"select * from {GOLD}.fct_anomaly_alerts order by severity, transaction_date, line_id")
history = read(f"select * from {SCD}.dim_cost_centers_history")

if gl is None or close is None:
    st.warning(
        "The Gold layer is not built yet, so no control results can be shown.\n\n"
        "```\ndbt build --profiles-dir .\n```"
    )
    feed = pd.read_csv(os.path.join(BASE, "seeds", "raw_transactions.csv"))
    with st.expander(f"Raw ERP feed as dbt sees it ({len(feed)} journal lines)", expanded=True):
        st.dataframe(feed, width="stretch")
        st.metric("Journal lines", len(feed))
        st.metric("Journals", int(feed["journal_id"].nunique()))
    st.stop()

# ---------------------------------------------------------------- KPI row
periods = pd.to_datetime(close["fiscal_month"]).dt.strftime("%Y-%m").tolist()
choice = st.sidebar.selectbox("Close period", periods, index=len(periods) - 1)
row = close[pd.to_datetime(close["fiscal_month"]).dt.strftime("%Y-%m") == choice].iloc[0]

st.subheader(f"{choice} close position")
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Journals", int(row["journal_count"]))
k2.metric("Unbalanced journals", int(row["unbalanced_journal_count"]))
k3.metric("Lines on orphan accounts", int(row["orphan_journal_count"]))
k4.metric("Manual lines", int(row["manual_line_count"]))
k5.metric(
    "Debits vs credits",
    f"${row['total_debits_usd']:,.2f}",
    help="Difference from credits is the unresolved break; it must be $0.00 to sign off.",
)
st.markdown(
    ":red[**BLOCKED - EXCEPTIONS OPEN**]  "
    if row["close_status"] != "READY FOR SIGN-OFF"
    else ":green[**READY FOR SIGN-OFF**]  "
)
if int(row["unbalanced_journal_count"]) or int(row["orphan_journal_count"]):
    st.caption(
        f"Ledger break this period: "
        f"${row['total_debits_usd'] - row['total_credits_usd']:,.2f}"
    )

# ---------------------------------------------------------------- close progress
st.subheader("Close progress by period (FC-01 / FC-02 evidence)")
st.dataframe(close, width="stretch")
chart = close.assign(
    period=pd.to_datetime(close["fiscal_month"]).dt.strftime("%Y-%m")
).set_index("period")[["total_debits_usd", "total_credits_usd"]]
st.bar_chart(chart)

# ---------------------------------------------------------------- anomaly alerts
st.subheader("Anomaly alerts - review before sign-off")
if alerts is None or len(alerts) == 0:
    st.success("No open anomaly alerts.")
else:
    severities = sorted(alerts["severity"].unique())
    picked = st.multiselect("Severity", severities, default=severities)
    shown = alerts[alerts["severity"].isin(picked)]
    st.dataframe(
        shown[
            [
                "severity",
                "anomaly_type",
                "journal_id",
                "line_id",
                "transaction_date",
                "account_id",
                "account_name",
                "signed_amount_usd",
                "created_by",
                "description",
                "recommended_action",
            ]
        ],
        width="stretch",
    )
    st.bar_chart(alerts["anomaly_type"].value_counts())

# ---------------------------------------------------------------- drill-down
st.subheader("Trace an exception to source")
if alerts is not None and len(alerts):
    journal = st.selectbox("Journal with an alert", sorted(alerts["journal_id"].unique()))
else:
    journal = st.selectbox("Journal", sorted(gl["journal_id"].unique())[:50])
lines = gl[gl["journal_id"] == journal]
st.dataframe(
    lines[
        [
            "line_id",
            "transaction_date",
            "account_id",
            "account_name",
            "currency_code",
            "fx_rate_to_usd",
            "debit_amount_usd",
            "credit_amount_usd",
            "signed_amount_usd",
            "source_system",
            "created_by",
            "is_orphan_account",
        ]
    ],
    width="stretch",
)
st.caption(
    f"Journal {journal} nets to ${lines['signed_amount_usd'].sum():,.2f}. "
    "Every column above traces back to one seed file in two hops - "
    "`seeds/raw_transactions.csv` -> `stg_erp__transactions` -> this table."
)

# ---------------------------------------------------------------- SCD2 history
if history is not None:
    st.subheader("Cost centre history (SCD Type 2)")
    valid_from = pd.to_datetime(history["dbt_valid_from"])
    valid_to = pd.to_datetime(history["dbt_valid_to"])
    # "As of" means end of day: a version created at 09:00 on the 31st was what
    # Finance reported against that month, so the cutoff is the 31st + 1 day.
    cutoff = pd.Timestamp(
        st.date_input("As of", value=valid_from.max().normalize().date())
    ) + pd.Timedelta(days=1)
    active = history[(valid_from < cutoff) & (valid_to.fillna(cutoff) >= cutoff)]
    st.dataframe(
        active[["cost_center_id", "cost_center_name", "region", "dbt_valid_from", "dbt_valid_to"]],
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "A GL line joined on `cost_center_id` and its own `transaction_date` resolves to the "
        "version Finance reported against at the time, not to today's org chart."
    )

with st.expander("How to clear an exception"):
    st.markdown(
        """
        1. Open the alert's `journal_id` in `fct_general_ledger` and trace it to the ERP source line.
        2. Fix at source (never edit Gold tables directly) and re-run `dbt build`.
        3. If the entry is legitimate, attach evidence and waive it in the close checklist -
           the alert history stays for auditors.

        Signed-off evidence produced by this repo: `target/manifest.json` (dbt run graph),
        `reports/gx_validation.json` (pre-dbt gate), `reports/close_metrics.json` (every
        published figure), `reports/incremental_and_scd2_proof.json` (materialisation proof).
        """
    )
