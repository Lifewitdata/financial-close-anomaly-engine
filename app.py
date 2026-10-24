"""
Streamlit - Month-End Close Monitoring Dashboard
Finance stakeholders see: close readiness, reconciliation breaks, and anomaly alerts.
Data source order: DuckDB gold tables (after `dbt build`) -> fallback to data/raw_transactions.csv
so the dashboard demos even before dbt is run.
Run: streamlit run app.py
"""
import os
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Financial Close & Anomaly Detection", layout="wide")
st.title("Automated Financial Close & Anomaly Detection Engine")
st.caption("dbt + Databricks (prod) / DuckDB (local)  •  Bronze → Silver → Gold  •  SOX controls FC-01 / FC-02")

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "financial_close.duckdb")

def load_from_duckdb():
    import duckdb
    con = duckdb.connect(DB, read_only=True)
    close = con.execute("select * from main_gold.fct_monthly_close_summary order by fiscal_month").df()
    alerts = con.execute("select * from main_gold.fct_anomaly_alerts order by severity, transaction_date").df()
    gl = con.execute("select * from main_gold.fct_general_ledger order by transaction_date").df()
    con.close()
    return close, alerts, gl

def load_from_csv():
    df = pd.read_csv(os.path.join(BASE, "data", "raw_transactions.csv"), parse_dates=["transaction_date"])
    df["fiscal_month"] = df["transaction_date"].dt.to_period("M").dt.to_timestamp()
    rec = df.groupby("journal_id").agg(total_d=("debit_amount","sum"), total_c=("credit_amount","sum")).reset_index()
    rec["is_unbalanced"] = (rec["total_d"]-rec["total_c"]).abs() > 0.01
    close = df.groupby("fiscal_month").size().reset_index(name="line_count")
    close["journal_count"] = df.groupby("fiscal_month")["journal_id"].nunique().values
    close["total_debits_usd"] = df.groupby("fiscal_month")["debit_amount"].sum().values
    close["total_credits_usd"] = df.groupby("fiscal_month")["credit_amount"].sum().values
    close["unbalanced_journal_count"] = rec.merge(df[["journal_id","fiscal_month"]].drop_duplicates(), on="journal_id").groupby("fiscal_month")["is_unbalanced"].sum().values
    close["orphan_journal_count"] = 1 if "9999" in df["account_id"].astype(str).values else 0
    close["manual_line_count"] = df[df["source_system"]=="MANUAL"].groupby("fiscal_month").size().reindex(close["fiscal_month"]).fillna(0).values
    close["close_status"] = close.apply(lambda r: "READY FOR SIGN-OFF" if r["unbalanced_journal_count"]==0 else "BLOCKED - EXCEPTIONS OPEN", axis=1)
    alerts = pd.DataFrame({"note": ["Run `dbt build` to generate the full anomaly alerts table (fct_anomaly_alerts). CSV fallback shows close summary only."]})
    return close, alerts, df

if os.path.exists(DB):
    try:
        close_df, alerts_df, gl_df = load_from_duckdb()
        st.success("Connected to DuckDB gold layer (dbt-built tables).")
    except Exception as e:
        st.warning(f"DuckDB found but gold tables not readable ({e}). Falling back to CSV.")
        close_df, alerts_df, gl_df = load_from_csv()
else:
    st.info("No DuckDB file yet - showing CSV fallback. Run `dbt seed && dbt build` for the full gold layer.")
    close_df, alerts_df, gl_df = load_from_csv()

# KPI row - latest month
latest = close_df.iloc[-1]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest period", pd.to_datetime(latest["fiscal_month"]).strftime("%Y-%m"))
c2.metric("Journals", int(latest["journal_count"]))
c3.metric("Unbalanced journals", int(latest["unbalanced_journal_count"]))
c4.metric("Close status", str(latest["close_status"]))

st.subheader("Close progress by month (SOX FC-01 evidence)")
st.dataframe(close_df, use_container_width=True)
st.bar_chart(close_df.set_index(pd.to_datetime(close_df["fiscal_month"]).dt.strftime("%Y-%m"))[["total_debits_usd","total_credits_usd"]])

st.subheader("Anomaly alerts - review before sign-off")
if "anomaly_type" in alerts_df.columns:
    sev = st.multiselect("Severity", sorted(alerts_df["severity"].unique()), default=sorted(alerts_df["severity"].unique()))
    st.dataframe(alerts_df[alerts_df["severity"].isin(sev)], use_container_width=True)
    st.bar_chart(alerts_df["anomaly_type"].value_counts())
else:
    st.dataframe(alerts_df, use_container_width=True)

st.subheader("How to clear an exception")
st.markdown("""
1. Open the alert's `journal_id` in `fct_general_ledger` and trace it to the ERP source line.
2. Fix at source (never edit Gold tables directly) and re-run `dbt build`.
3. If the entry is legitimate, attach evidence and waive it in the close checklist - the alert history stays for auditors.
""")
