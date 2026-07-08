"""
Reporter — builds Slack Block Kit messages, posts them, and generates PDF reports.

PASS day  → single quiet green line.
FAIL day  → full breakdown: per-check status, gap, severity, Claude triage.
PDF       → generated on every run, saved to /dbfs/tmp/validation_reports/.
"""

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


# ── PDF report ────────────────────────────────────────────────────────────────

def generate_pdf_report(run_result: Dict, output_dir: str = "/dbfs/tmp/validation_reports") -> str:
    """Generate a PDF validation report and return the saved file path."""
    try:
        from fpdf import FPDF
    except ImportError:
        print("[reporter] fpdf2 not installed — skipping PDF. Run: pip install fpdf2")
        return ""

    dashboard = run_result["dashboard"]
    run_week  = run_result["run_week"]
    overall   = run_result["overall_status"]
    summary   = run_result["summary"]
    results: List[CheckResult] = run_result["results"]
    triage    = run_result.get("triage_analysis", "")
    timestamp = run_result.get("run_timestamp", "")

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{dashboard}_{run_week}.pdf")

    # colors
    GREEN      = (34, 139, 34)
    RED        = (200, 50, 50)
    ORANGE     = (210, 120, 0)
    WHITE      = (255, 255, 255)
    DARK       = (30, 30, 30)
    LIGHT_GRAY = (245, 245, 245)
    MID_GRAY   = (150, 150, 150)

    status_color = {"PASS": GREEN, "FAIL": RED, "DRIFT": ORANGE}
    status_str   = str(overall).replace("Status.", "")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # header bar
    pdf.set_fill_color(*DARK)
    pdf.rect(0, 0, 210, 28, "F")
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(10, 7)
    pdf.cell(0, 8, "Dashboard Validation Report", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(10, 18)
    pdf.cell(0, 5, f"Generated: {timestamp}", ln=True)
    pdf.set_text_color(*DARK)
    pdf.ln(8)

    # dashboard info
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, dashboard.replace("_", " ").title(), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Run Week: {run_week}", ln=True)
    pdf.ln(4)

    # overall status box
    color = status_color.get(status_str, DARK)
    pdf.set_fill_color(*color)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 13, f"   Overall Status: {status_str}", ln=True, fill=True)
    pdf.set_text_color(*DARK)
    pdf.ln(5)

    # summary counts
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Summary", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(55, 7, f"Total Checks: {summary['total']}", border=1)
    pdf.set_text_color(*GREEN)
    pdf.cell(45, 7, f"PASS: {summary['pass']}", border=1)
    pdf.set_text_color(*ORANGE)
    pdf.cell(45, 7, f"DRIFT: {summary['drift']}", border=1)
    pdf.set_text_color(*RED)
    pdf.cell(45, 7, f"FAIL: {summary['fail']}", border=1, ln=True)
    pdf.set_text_color(*DARK)
    pdf.ln(6)

    # check details table
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Check Details", ln=True)

    pdf.set_fill_color(*DARK)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(32, 7, "Check",  border=1, fill=True)
    pdf.cell(52, 7, "Metric", border=1, fill=True)
    pdf.cell(18, 7, "Status", border=1, fill=True)
    pdf.cell(88, 7, "Detail", border=1, fill=True, ln=True)
    pdf.set_text_color(*DARK)

    for i, r in enumerate(results):
        row_fill = i % 2 == 0
        pdf.set_fill_color(*(LIGHT_GRAY if row_fill else WHITE))

        r_status = str(r.status).replace("Status.", "")
        r_color  = status_color.get(r_status, DARK)

        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK)
        pdf.cell(32, 6, r.check_name,      border=1, fill=row_fill)
        pdf.cell(52, 6, r.metric[:30],      border=1, fill=row_fill)
        pdf.set_text_color(*r_color)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(18, 6, r_status,           border=1, fill=row_fill)
        pdf.set_text_color(*DARK)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(88, 6, (r.detail or "")[:55], border=1, fill=row_fill, ln=True)

    pdf.ln(6)

    # Claude triage section (FAIL/DRIFT only)
    if triage:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Claude AI — Root Cause Analysis", ln=True)
        pdf.set_fill_color(255, 250, 220)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, triage.strip(), border=1, fill=True)
        pdf.ln(4)

    # footer
    pdf.set_y(-15)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(0, 5, "Generated by Dashboard Validation Framework  |  LatentView Analytics", align="C")

    pdf.output(output_path)
    print(f"[reporter] PDF saved: {output_path}")
    return output_path
