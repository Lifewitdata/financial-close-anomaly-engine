"""
Render the dbt lineage graph to docs/dbt-lineage.png.

`dbt docs generate --static` produces a self-contained target/static_index.html (2.6 MB -
built, not committed). This opens it in headless Chrome, clicks the lineage launcher and
screenshots the graph, so the DAG is visible in the README without a reviewer running
anything. scripts/build_lineage.py writes the same graph as Mermaid in LINEAGE.md; this
script is the picture, that one is the text a diff can review.

Usage:    python scripts/capture_dbt_docs.py
Requires: pip install -r requirements-dev.txt, dbt build --profiles-dir .
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "dbt-lineage.png"


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "docs", "generate", "--static",
         "--profiles-dir", str(ROOT), "--project-dir", str(ROOT), "--no-use-colors"],
        cwd=str(ROOT), check=True,
    )
    doc = ROOT / "target" / "static_index.html"
    if not doc.exists():
        raise SystemExit("dbt did not produce target/static_index.html")

    from playwright.sync_api import sync_playwright

    OUT.parent.mkdir(exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1050},
                                device_scale_factor=2)
        page.goto(doc.as_uri(), wait_until="load", timeout=60_000)
        page.wait_for_timeout(5000)
        page.click(".launcher-btn", timeout=20_000)
        page.wait_for_selector("#graph-viz-wrapper canvas", timeout=30_000)
        # Cytoscape lays the graph out asynchronously; screenshotting early gives a
        # panel with a handful of nodes in the top-left corner.
        page.wait_for_timeout(6000)
        page.locator(".launcher-window").screenshot(path=str(OUT))
        browser.close()

    size = OUT.stat().st_size
    if size < 60_000:
        raise SystemExit(f"{OUT} is only {size} bytes - the graph did not render")
    print(f"{OUT.relative_to(ROOT)}: {size} bytes at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
