"""
Reporter — builds Slack Block Kit messages and posts them.

PASS day  → single quiet green line.
FAIL day  → full breakdown: per-check status, gap, severity, Claude triage.
"""

import json
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
