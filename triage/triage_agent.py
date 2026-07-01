"""
Triage agent — calls Claude to explain why a validation run failed.

Called only when overall_status is FAIL or DRIFT. Returns a plain-English
analysis that is attached to the Slack report and Databricks notebook output.

The Anthropic API key must be available as the environment variable
ANTHROPIC_API_KEY, or passed explicitly to run_triage().
"""

import os
from typing import Dict, List, Optional

from checks import CheckResult, Status


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(run_result: Dict) -> str:
    dashboard = run_result["dashboard"]
    run_week  = run_result["run_week"]
    results: List[CheckResult] = run_result["results"]

    failed = [r for r in results if r.status in (Status.FAIL, Status.DRIFT)]

    lines = []
    for r in failed:
        lines.append(
            f"  - check: {r.check_name}  |  metric: {r.metric}  |  status: {r.status}\n"
            f"    expected: {r.expected}  |  got: {r.actual}\n"
            f"    detail: {r.detail}"
        )

    return f"""You are a data quality analyst reviewing a failed dashboard validation.

Dashboard  : {dashboard}
Fiscal week: {run_week}

Failed / drifted checks:
{chr(10).join(lines)}

Provide a concise analysis (3–4 sentences) covering:
1. The most likely root cause (upstream data gap, pipeline bug, mapping issue, business event, etc.).
2. Which table or system to investigate first.
3. A suggested action with a clear owner.

If the check details are insufficient to determine the root cause, say so explicitly
and describe what additional context to look at. Do not speculate beyond what the
numbers show."""


# ── main entry point ──────────────────────────────────────────────────────────

def run_triage(run_result: Dict, api_key: Optional[str] = None) -> str:
    """Return a plain-English explanation of the validation failure.

    Returns an empty string if the run passed (no triage needed).
    Returns a bracketed warning string if the API call fails or is not configured.
    """
    if run_result["overall_status"] == Status.PASS:
        return ""

    try:
        import anthropic
    except ImportError:
        return (
            "[triage] anthropic package not installed. "
            "Run: pip install anthropic  then re-run the job."
        )

    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        return (
            "[triage] ANTHROPIC_API_KEY environment variable not set. "
            "Add it to your Databricks cluster environment or Secret scope."
        )

    client = anthropic.Anthropic(api_key=resolved_key)
    prompt = _build_prompt(run_result)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = message.content[0].text.strip()
        print(f"[triage] Claude analysis received ({len(analysis)} chars)")
        return analysis

    except Exception as exc:
        return f"[triage] Claude API call failed: {exc}"
