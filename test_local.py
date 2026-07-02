"""
Local test — no Spark, no Databricks needed.

Tests:
  1. YAML registry loads and has required keys
  2. CheckResult dataclass works correctly
  3. Reporter builds correct Slack messages (PASS and FAIL)
  4. Triage prompt builder produces a non-empty prompt
  5. All imports resolve without error

Run from the repo root:
    cd dashboard_validation_framework
    python test_local.py
"""

import sys, os
sys.stdout.reconfigure(encoding='utf-8')

# Make engine/ and triage/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "triage"))

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "registry", "social_hub_consolidated_dashboard.yaml")

PASS  = "\033[92m  PASS\033[0m"
FAIL  = "\033[91m  FAIL\033[0m"

results = []

def check(name, fn):
    try:
        fn()
        print(f"{PASS}  {name}")
        results.append(True)
    except Exception as e:
        print(f"{FAIL}  {name}")
        print(f"         Error: {e}")
        results.append(False)

print("\n" + "═" * 60)
print("  Dashboard Validation Framework — Local Tests")
print("═" * 60 + "\n")

# ── Test 1: imports ──────────────────────────────────────────────────────────
def t_imports():
    import yaml
    from checks import CheckResult, Status
    from reporter import build_slack_message, print_console_report
    from triage_agent import _build_prompt

check("All imports resolve (checks, reporter, triage_agent, yaml)", t_imports)

# ── Test 2: YAML loads ───────────────────────────────────────────────────────
def t_yaml_loads():
    import yaml
    with open(REGISTRY_PATH) as f:
        reg = yaml.safe_load(f)
    assert "dashboard" in reg,       "Missing 'dashboard' key"
    assert "dashboard_table" in reg, "Missing 'dashboard_table' key"
    # Accept both old-style (source_table) and new-style (source_tables list)
    has_source = "source_table" in reg or "source_tables" in reg
    assert has_source, "Missing 'source_table' or 'source_tables' key"
    assert "metrics" in reg,         "Missing 'metrics' key"
    assert len(reg["metrics"]) > 0,  "metrics list is empty"

check("YAML registry loads and has required keys", t_yaml_loads)

# ── Test 3: YAML metric structure ────────────────────────────────────────────
def t_yaml_metrics():
    import yaml
    with open(REGISTRY_PATH) as f:
        reg = yaml.safe_load(f)
    for m in reg["metrics"]:
        assert "name" in m, f"Metric missing 'name': {m}"
        # Accept both new-style (tolerance: 1.0%) and old-style (tolerance_pct: 1.0)
        has_tol = "tolerance" in m or "tolerance_pct" in m
        assert has_tol, f"Metric '{m['name']}' missing 'tolerance' or 'tolerance_pct'"
        assert "checks" in m, f"Metric '{m['name']}' missing 'checks'"
    metric_names = [m["name"] for m in reg["metrics"]]
    print(f"         Metrics defined: {metric_names}")

check("All YAML metrics have name, tolerance or tolerance_pct, checks", t_yaml_metrics)

# ── Test 4: CheckResult dataclass ────────────────────────────────────────────
def t_check_result():
    from checks import CheckResult, Status
    r = CheckResult(
        check_name="reconciliation",
        metric="impressions",
        status=Status.PASS,
        expected=1_000_000,
        actual=1_005_000,
        gap=5000,
        tolerance=1.0,
        detail="Gap: 0.5%  (tolerance: ±1.0%)"
    )
    assert r.status == Status.PASS
    assert r.severity is None          # PASS has no severity
    f = CheckResult("freshness", "row_freshness", Status.FAIL, "2026-23", "2026-22", detail="Stale")
    assert f.severity == "P2"
    d = CheckResult("trend_sanity", "impressions", Status.DRIFT, 1_000_000, 1_400_000, detail="WoW +40%")
    assert d.severity == "P3"

check("CheckResult: PASS/DRIFT/FAIL statuses and severity labels", t_check_result)

# ── Test 5: Reporter — PASS message ─────────────────────────────────────────
def t_reporter_pass():
    from checks import CheckResult, Status
    from reporter import build_slack_message

    run_result = {
        "dashboard":      "test_dashboard",
        "run_week":       "2026-23",
        "run_timestamp":  "2026-07-01T06:00:00",
        "summary":        {"total": 3, "pass": 3, "drift": 0, "fail": 0},
        "overall_status": Status.PASS,
        "results": [
            CheckResult("freshness",      "row_freshness", Status.PASS, "2026-23", "2026-23"),
            CheckResult("reconciliation", "impressions",   Status.PASS, 1_000_000, 1_005_000),
            CheckResult("completeness",   "platform_completeness", Status.PASS, 8, 8),
        ],
        "triage_analysis": "",
    }
    msg = build_slack_message(run_result)
    assert "blocks" in msg
    assert "PASS" in msg["text"]
    assert "3 / 3" in msg["text"] or "3/3" in msg["text"]
    print(f"         Slack text: {msg['text']}")

check("Reporter builds correct PASS Slack message", t_reporter_pass)

# ── Test 6: Reporter — FAIL message ─────────────────────────────────────────
def t_reporter_fail():
    from checks import CheckResult, Status
    from reporter import build_slack_message

    run_result = {
        "dashboard":      "test_dashboard",
        "run_week":       "2026-23",
        "run_timestamp":  "2026-07-01T06:00:00",
        "summary":        {"total": 3, "pass": 2, "drift": 0, "fail": 1},
        "overall_status": Status.FAIL,
        "results": [
            CheckResult("freshness",      "row_freshness", Status.PASS, "2026-23", "2026-23"),
            CheckResult("reconciliation", "impressions",   Status.FAIL, 1_000_000, 850_000,
                        gap=-150_000, tolerance=1.0, detail="Gap: 15.0%  (tolerance: ±1.0%)"),
            CheckResult("completeness",   "platform_completeness", Status.PASS, 8, 8),
        ],
        "triage_analysis": "APAC partition missing from last night's load.",
    }
    msg = build_slack_message(run_result)
    assert "blocks" in msg
    assert "FAIL" in msg["text"]
    # Triage text must appear in the blocks
    combined = " ".join(
        b.get("text", {}).get("text", "")
        for b in msg["blocks"] if isinstance(b.get("text"), dict)
    )
    assert "APAC" in combined, "Triage text not found in Slack blocks"
    assert "impressions" in combined, "Failing metric not in Slack blocks"
    print(f"         Slack text: {msg['text']}")

check("Reporter builds correct FAIL Slack message with triage text", t_reporter_fail)

# ── Test 7: Console report prints without error ──────────────────────────────
def t_console_report():
    from checks import CheckResult, Status
    from reporter import print_console_report
    import io, contextlib

    run_result = {
        "dashboard":      "test_dashboard",
        "run_week":       "2026-23",
        "run_timestamp":  "2026-07-01T06:00:00",
        "summary":        {"total": 2, "pass": 1, "drift": 0, "fail": 1},
        "overall_status": Status.FAIL,
        "results": [
            CheckResult("freshness",      "row_freshness", Status.PASS, "2026-23", "2026-23"),
            CheckResult("reconciliation", "impressions",   Status.FAIL, 1_000_000, 850_000,
                        gap=-150_000, tolerance=1.0, detail="Gap: 15.0%"),
        ],
        "triage_analysis": "Root cause: missing partition.",
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_console_report(run_result)
    output = buf.getvalue()
    assert "FAIL" in output
    assert "impressions" in output
    assert "Root cause" in output

check("Console report prints PASS/FAIL breakdown and triage text", t_console_report)

# ── Test 8: Triage prompt builder ────────────────────────────────────────────
def t_triage_prompt():
    from checks import CheckResult, Status
    from triage_agent import _build_prompt

    run_result = {
        "dashboard": "social_hub_consolidated_dashboard",
        "run_week":  "2026-23",
        "results": [
            CheckResult("reconciliation", "impressions", Status.FAIL, 1_000_000, 850_000,
                        gap=-150_000, tolerance=1.0, detail="Gap: 15.0%  (tolerance: ±1.0%)"),
        ],
        "overall_status": Status.FAIL,
    }
    prompt = _build_prompt(run_result)
    assert "social_hub_consolidated_dashboard" in prompt
    assert "2026-23" in prompt
    assert "impressions" in prompt
    assert len(prompt) > 200, "Prompt too short"
    print(f"         Prompt length: {len(prompt)} chars")

check("Triage prompt builder includes dashboard, week, and failing check details", t_triage_prompt)

# ── Test 9: ValidationEngine — YAML load only (no Spark) ────────────────────
def t_engine_yaml_load():
    import yaml
    from pathlib import Path
    path = Path(REGISTRY_PATH)
    assert path.exists(), f"Registry file not found: {REGISTRY_PATH}"
    with open(path) as f:
        reg = yaml.safe_load(f)
    assert reg["dashboard"] == "social_hub_consolidated_dashboard"
    assert reg["dashboard_table"].startswith("socialmedia.")
    # Accept both source_table (old) and source_tables (new list)
    if "source_tables" in reg:
        assert isinstance(reg["source_tables"], list) and len(reg["source_tables"]) > 0
        assert reg["source_tables"][0].startswith("socialmedia.")
        src_display = reg["source_tables"][0]
    else:
        assert reg["source_table"].startswith("socialmedia.")
        src_display = reg["source_table"]
    print(f"         Dashboard table : {reg['dashboard_table']}")
    print(f"         Source table    : {src_display}")
    print(f"         Metrics         : {[m['name'] for m in reg['metrics']]}")
    # Validate new-style metrics if present
    for m in reg["metrics"]:
        tol_raw = m.get("tolerance", m.get("tolerance_pct", "1.0"))
        tol = float(str(tol_raw).rstrip("%"))
        checks = ["trend_sanity" if c == "trend" else c for c in m.get("checks", [])]
        assert all(c in ("freshness","reconciliation","parts_sum","trend_sanity","completeness")
                   for c in checks), f"Unknown check type in metric {m['name']}: {checks}"

check("ValidationEngine: YAML parses correctly with correct table names", t_engine_yaml_load)

# ── Summary ──────────────────────────────────────────────────────────────────
passed = sum(results)
total  = len(results)
print()
print("═" * 60)
if passed == total:
    print(f"\033[92m  All {total} tests passed.\033[0m")
else:
    print(f"\033[91m  {passed}/{total} tests passed. Fix errors above before running on Databricks.\033[0m")
print("═" * 60 + "\n")

sys.exit(0 if passed == total else 1)
