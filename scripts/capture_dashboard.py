"""
Serve the dashboard and screenshot it, so "launch-verified" is a file and not a claim.

Starts a real `streamlit run`, waits on /_stcore/health, then drives headless Chrome
through Playwright. Playwright (not `chrome --screenshot`) is required because Streamlit
fills the page over a websocket after the load event: a plain CLI screenshot captures the
grey skeleton and calls it a success. This script waits for the KPI values to appear and
asserts they are the same numbers `scripts/verify_close.py` locks against the warehouse.

Writes docs/dashboard.png + reports/streamlit_launch.json.

Usage:      python scripts/capture_dashboard.py
Requires:   pip install -r requirements-dev.txt, and `dbt build --profiles-dir .`
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:0"  # replaced in main() once a free port is known

# The figures a reviewer should see in the screenshot. Kept in one place so the
# README, verify_close.py and this script cannot drift apart silently.
EXPECTED_METRICS = {
    "Journals": "51",
    "Unbalanced journals": "1",
    "Lines on orphan accounts": "1",
    "Manual lines": "10",
    "Debits vs credits": "$1,759,359.41",
}
EXPECTED_TEXT = [
    "2026-03 close position",
    "BLOCKED - EXCEPTIONS OPEN",
    "Ledger break this period: $5,000.00",
    "Cost centre history (SCD Type 2)",
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(proc: subprocess.Popen) -> int:
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"streamlit exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(f"{BASE}/_stcore/health", timeout=2) as r:
                if r.status == 200 and r.read() == b"ok":
                    return r.status
        except (urllib.error.URLError, ConnectionResetError, TimeoutError):
            time.sleep(0.5)
    raise SystemExit("streamlit did not become healthy within 90s")


def launch_chromium(p):
    """Prefer the Chrome the machine already has; fall back to Playwright's own build."""
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


def render(page) -> dict:
    """Load the app, block until the data-driven widgets exist, return what was drawn."""
    page.goto(BASE, wait_until="load", timeout=60_000)
    page.wait_for_selector('[data-testid="stMetricValue"]', timeout=90_000)
    # Charts are Vega-Lite renders that land after the metric widgets, so give the
    # websocket one more settle window before capturing; otherwise the screenshot shows
    # empty chart frames next to populated numbers.
    page.wait_for_timeout(4000)
    pairs = page.eval_on_selector_all(
        '[data-testid="stMetric"]',
        "els => els.map(e => [e.querySelector('[data-testid=\"stMetricLabel\"]')"
        ".textContent.trim(), e.querySelector('[data-testid=\"stMetricValue\"]')"
        ".textContent.trim()])",
    )
    body = page.evaluate("() => document.body.innerText")
    rows = page.eval_on_selector_all(
        '[data-testid="stDataFrame"]',
        "els => els.map(e => e.querySelectorAll('[role=row]').length)",
    )
    # Streamlit scrolls inside its own container, so full_page=True only ever captures
    # the first viewport and the app's own height is pinned to 100vh. Find the real
    # scroll height, then grow the viewport so everything fits without scrolling.
    height = page.evaluate(
        """() => {
            const scrollers = [...document.querySelectorAll('*')]
                .filter(e => e.scrollHeight > e.clientHeight + 50)
                .map(e => e.scrollHeight);
            return Math.ceil(Math.max(900, ...scrollers)) + 60;
        }"""
    )
    page.set_viewport_size({"width": 1680, "height": min(height, 4400)})
    page.wait_for_timeout(1500)  # let the charts re-layout for the new width
    page.screenshot(path=str(ROOT / "docs" / "dashboard.png"))
    return {
        "metrics": dict(pairs),
        "dataframe_rows": rows,
        "missing_text": [t for t in EXPECTED_TEXT if t not in body],
        "content_height_px": height,
    }


def main() -> int:
    global BASE
    BASE = f"http://127.0.0.1:{free_port()}"
    env = dict(os.environ, FINANCIAL_CLOSE_DUCKDB=str(ROOT / "financial_close.duckdb"))
    server_log = ROOT / ".streamlit_server.log"
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    png = ROOT / "docs" / "dashboard.png"
    png.unlink(missing_ok=True)

    with open(server_log, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
             "--server.headless", "true",
             "--server.port", BASE.rsplit(":", 1)[1],
             "--server.address", "127.0.0.1",
             "--browser.gatherUsageStats", "false",
             "--server.fileWatcherType", "none"],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            status = wait_for_server(proc)
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = launch_chromium(p)
                page = browser.new_page(viewport={"width": 1680, "height": 1100},
                                        device_scale_factor=2)
                observed = render(page)
                browser.close()
        finally:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()

    tail = server_log.read_text(encoding="utf-8", errors="replace")[-1200:]
    server_log.unlink(missing_ok=True)

    mismatch = {k: (v, observed["metrics"].get(k)) for k, v in EXPECTED_METRICS.items()
                if observed["metrics"].get(k) != v}
    size = png.stat().st_size if png.exists() else 0
    if mismatch or observed["missing_text"] or size < 100_000:
        raise SystemExit(
            "dashboard did not render the expected Gold-table figures.\n"
            f"screenshot bytes: {size}\n"
            f"unexpected/missing KPIs (expected, rendered): {json.dumps(mismatch, indent=2)}\n"
            f"missing page text: {observed['missing_text']}\n"
            f"rendered KPIs: {json.dumps(observed['metrics'], indent=2)}\n"
            f"--- streamlit log tail ---\n{tail}"
        )

    report = {
        "served_url": BASE,
        "health_status": status,
        "screenshot": "docs/dashboard.png",
        "bytes": size,
        "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
        "duckdb": env["FINANCIAL_CLOSE_DUCKDB"],
        "rendered_metrics": observed["metrics"],
        "expected_text_found": EXPECTED_TEXT,
        "dataframe_rows": observed["dataframe_rows"],
        "content_height_px": observed["content_height_px"],
        "expected_metrics_matched": True,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (ROOT / "reports" / "streamlit_launch.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
