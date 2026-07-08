"""
Reporter — builds Slack Block Kit messages and posts them.

PASS day  → single quiet green line.
FAIL day  → full breakdown: per-check status, gap, severity, Claude triage.

Also provides file reports:
  save_csv_report()  → appends one row per check to a running history CSV
  save_html_report() → self-contained, manager-friendly HTML summary
"""

import csv
import html as html_mod
import json
import os
from typing import Dict, List, Optional

import requests

from checks import CheckResult, Status


# ── message builders ──────────────────────────────────────────────────────────

def build_slack_message(run_result: Dict) -> Dict:
    """Return a Slack Block Kit payload for the given run result."""
    if run_result["overall_status"] == Status.PASS:
        return _pass_message(run_result)
    return _fail_message(run_result)


def _pass_message(run_result: Dict) -> Dict:
    s = run_result["summary"]
    dashboard = run_result["dashboard"]
    ts = run_result["run_timestamp"]
    return {
        "text": f":white_check_mark: {dashboard} — {s['total']}/{s['total']} checks PASS",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark:  *{dashboard}*\n"
                        f"{s['total']} / {s['total']} checks PASS\n"
                        f"_Pre-refresh check · {ts}_"
                    ),
                },
            }
        ],
    }


def _fail_message(run_result: Dict) -> Dict:
    dashboard = run_result["dashboard"]
    run_week  = run_result["run_week"]
    s         = run_result["summary"]
    ts        = run_result["run_timestamp"]
    results: List[CheckResult] = run_result["results"]
    triage: str = run_result.get("triage_analysis", "")

    overall_icon = ":red_circle:" if run_result["overall_status"] == Status.FAIL else ":large_yellow_circle:"

    header = (
        f"{overall_icon}  *Dashboard validation — {dashboard}*\n"
        f"Pre-refresh check · {ts}\n"
        f"*{s['fail']} FAIL*  ·  {s['drift']} DRIFT  ·  {s['pass']} PASS"
    )

    check_blocks = []
    for r in results:
        if r.status == Status.PASS:
            continue

        icon = ":x:" if r.status == Status.FAIL else ":warning:"
        gap_str = ""
        if r.gap is not None and r.tolerance is not None:
            gap_str = f"   Gap: `{r.gap:+,.0f}`   Tolerance: ±{r.tolerance}%"

        check_blocks.append({"type": "divider"})
        check_blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{icon} *{r.metric}*   `{r.status}`"
                        + (f"   severity {r.severity}" if r.severity else "")
                        + f"\nExpected: `{r.expected}`   Got: `{r.actual}`{gap_str}\n"
                        f"_{r.detail}_"
                    ),
                },
            }
        )

    blocks: List[Dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        *check_blocks,
    ]

    if triage:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Claude's analysis — why it failed*\n{triage}",
                },
            }
        )

    return {
        "text": f"{overall_icon} {dashboard} — {s['fail']} FAIL on {run_week}",
        "blocks": blocks,
    }


# ── delivery ──────────────────────────────────────────────────────────────────

def send_slack_report(run_result: Dict, webhook_url: str) -> None:
    """POST the Slack report to webhook_url. Prints status to stdout."""
    payload = build_slack_message(run_result)
    resp = requests.post(
        webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if resp.status_code == 200:
        print(f"[reporter] Slack report sent: {run_result['overall_status']}")
    else:
        print(f"[reporter] Slack POST failed: {resp.status_code} — {resp.text}")


# ── CSV history log ────────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "run_timestamp", "run_week", "dashboard", "overall_status",
    "check_name", "metric", "status", "severity",
    "expected", "actual", "gap", "tolerance", "detail",
]


def save_csv_report(run_result: Dict, path: str = "validation_history.csv") -> str:
    """
    Append one row per check to a running CSV log. Each weekly run adds its
    rows, so the file becomes a filterable history (pivot by dashboard, week,
    check type, status) in Excel. Creates the file with a header on first run.
    Returns the path written.
    """
    results: List[CheckResult] = run_result["results"]
    file_exists = os.path.exists(path) and os.path.getsize(path) > 0

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(_CSV_COLUMNS)
        for r in results:
            writer.writerow([
                run_result["run_timestamp"],
                run_result["run_week"],
                run_result["dashboard"],
                run_result["overall_status"],
                r.check_name,
                r.metric,
                r.status,
                r.severity or "",
                r.expected,
                r.actual,
                r.gap if r.gap is not None else "",
                r.tolerance if r.tolerance is not None else "",
                r.detail,
            ])

    print(f"[reporter] CSV history appended: {path} (+{len(results)} rows)")
    return path


# ── HTML report (manager-friendly) ─────────────────────────────────────────────

_STATUS_COLOR = {Status.PASS: "#16a34a", Status.DRIFT: "#d97706", Status.FAIL: "#dc2626"}


def save_html_report(run_result: Dict, path: Optional[str] = None) -> str:
    """
    Write a self-contained HTML report: overall verdict, summary tiles, a
    colour-coded per-check table, and Claude's triage analysis when present.
    No dependencies, opens in any browser, prints cleanly. Returns the path.
    """
    s         = run_result["summary"]
    dashboard = run_result["dashboard"]
    run_week  = run_result["run_week"]
    overall   = run_result["overall_status"]
    ts        = run_result["run_timestamp"]
    results: List[CheckResult] = run_result["results"]
    triage: str = run_result.get("triage_analysis", "") or ""

    if path is None:
        path = f"validation_report_{dashboard}_{run_week}.html"

    esc = html_mod.escape
    overall_color = _STATUS_COLOR[overall]

    rows = []
    for r in results:
        color = _STATUS_COLOR[r.status]
        gap = f"{r.gap:+,.0f}" if r.gap is not None else "—"
        tol = f"±{r.tolerance}%" if r.tolerance is not None else "—"
        rows.append(
            f"<tr>"
            f"<td>{esc(r.check_name)}</td>"
            f"<td>{esc(r.metric)}</td>"
            f"<td><span class='badge' style='background:{color}'>{esc(str(r.status))}</span></td>"
            f"<td>{esc(str(r.severity) if r.severity else '—')}</td>"
            f"<td class='num'>{esc(str(r.expected))}</td>"
            f"<td class='num'>{esc(str(r.actual))}</td>"
            f"<td class='num'>{esc(gap)}</td>"
            f"<td class='num'>{esc(tol)}</td>"
            f"<td class='detail'>{esc(r.detail)}</td>"
            f"</tr>"
        )

    triage_html = ""
    if triage:
        triage_html = (
            "<h2>Root-cause analysis</h2>"
            f"<div class='triage'>{esc(triage).replace(chr(10), '<br>')}</div>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Validation — {esc(dashboard)} — {esc(run_week)}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; color: #1e293b;
         max-width: 1080px; margin: 32px auto; padding: 0 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 2px; }}
  .meta {{ color: #64748b; font-size: 13px; margin-bottom: 20px; }}
  .verdict {{ display: inline-block; padding: 4px 14px; border-radius: 6px;
              color: #fff; font-weight: 600; background: {overall_color}; }}
  .tiles {{ display: flex; gap: 12px; margin: 18px 0 26px; }}
  .tile {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 22px;
           text-align: center; }}
  .tile b {{ display: block; font-size: 26px; }}
  .tile span {{ font-size: 12px; color: #64748b; letter-spacing: .05em; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 10px; background: #f1f5f9;
        border-bottom: 2px solid #e2e8f0; font-size: 11px;
        text-transform: uppercase; letter-spacing: .05em; color: #475569; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  td.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td.detail {{ color: #64748b; }}
  .badge {{ color: #fff; padding: 2px 9px; border-radius: 5px;
            font-size: 11px; font-weight: 600; }}
  .triage {{ border: 1px solid #e2e8f0; border-left: 4px solid #6366f1;
             border-radius: 6px; padding: 14px 16px; font-size: 13px;
             line-height: 1.6; background: #fafafa; }}
  h2 {{ font-size: 15px; margin-top: 30px; }}
  @media print {{ body {{ margin: 0; }} }}
</style></head><body>
<h1>Dashboard validation — {esc(dashboard)}</h1>
<div class="meta">Week {esc(run_week)} · Pre-refresh check · {esc(ts)}</div>
<div class="verdict">{esc(str(overall))}</div>
<div class="tiles">
  <div class="tile"><b style="color:#16a34a">{s['pass']}</b><span>PASS</span></div>
  <div class="tile"><b style="color:#d97706">{s['drift']}</b><span>DRIFT</span></div>
  <div class="tile"><b style="color:#dc2626">{s['fail']}</b><span>FAIL</span></div>
  <div class="tile"><b>{s['total']}</b><span>TOTAL</span></div>
</div>
<table>
<thead><tr><th>Check</th><th>Metric</th><th>Status</th><th>Severity</th>
<th>Expected</th><th>Actual</th><th>Gap</th><th>Tolerance</th><th>Detail</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
{triage_html}
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)

    print(f"[reporter] HTML report written: {path}")
    return path


# ── console output (for Databricks notebook runs / local testing) ─────────────

def print_console_report(run_result: Dict) -> None:
    """Print a human-readable summary to stdout."""
    s         = run_result["summary"]
    dashboard = run_result["dashboard"]
    run_week  = run_result["run_week"]
    overall   = run_result["overall_status"]
    results: List[CheckResult] = run_result["results"]

    icons = {Status.PASS: "✓", Status.DRIFT: "~", Status.FAIL: "✗"}

    print("═" * 70)
    print(f"  Dashboard : {dashboard}")
    print(f"  Week      : {run_week}")
    print(f"  Result    : {overall}")
    print(f"  Summary   : {s['pass']} PASS  ·  {s['drift']} DRIFT  ·  {s['fail']} FAIL")
    print("═" * 70)

    for r in results:
        icon = icons.get(r.status, "?")
        print(f"  {icon} [{r.check_name}] {r.metric}: {r.status}")
        if r.status != Status.PASS:
            print(f"      Expected : {r.expected}")
            print(f"      Actual   : {r.actual}")
            print(f"      Detail   : {r.detail}")

    triage = run_result.get("triage_analysis")
    if triage:
        print("\n  Claude's analysis:")
        for line in triage.strip().splitlines():
            print(f"  {line}")

    print("═" * 70)
