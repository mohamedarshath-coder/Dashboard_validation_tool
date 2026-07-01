# Dashboard Validation Framework

**Catch dashboard problems before analysts and stakeholders do.**

A human-approved, AI-assisted validation engine that runs before every
dashboard refresh. Claude learns what to check once; a deterministic
Databricks job carries every week.

---

## How it works

```
SETUP (once)                          EVERY WEEK (deterministic)
─────────────────────                 ───────────────────────────────
Dashboard screenshot                  Databricks job reads registry YAML
       ↓                                          ↓
Claude derives spec                   Recomputes metrics from source tables
       ↓                                          ↓
You review & lock YAML                Compares vs dashboard → PASS/DRIFT/FAIL
  (registry/)                                     ↓
                                      Slack report posted
                                      On FAIL → Claude explains why
```

---

## Folder structure

```
dashboard_validation_framework/
├── registry/
│   └── <dashboard_name>.yaml       # one file per dashboard (human-approved)
├── engine/
│   ├── checks.py                   # 5 check types (freshness, reconciliation, …)
│   ├── validation_engine.py        # reads YAML, orchestrates checks
│   ├── reporter.py                 # Slack Block Kit + console output
│   └── validator.ipynb             # Databricks entry point — schedule this
├── triage/
│   └── triage_agent.py             # calls Claude API on FAIL to explain why
├── onboarding/
│   ├── ONBOARD_NEW_DASHBOARD.md    # step-by-step onboarding guide
│   └── onboard-dashboard.md        # Claude Code slash-command skill
└── requirements.txt
```

---

## Quick start — adding this to a new project

### 1. Copy the folder

Drop `dashboard_validation_framework/` into the root of your project repo.

### 2. Create a registry YAML for your dashboard

The easiest way is to use the Claude Code skill (see
[onboarding/ONBOARD_NEW_DASHBOARD.md](onboarding/ONBOARD_NEW_DASHBOARD.md)).

Or copy `registry/social_hub_consolidated_dashboard.yaml` and edit it:

```yaml
dashboard: my_dashboard_name
dashboard_table: myschema.my_dashboard
source_table:    myschema.my_source_silver

metrics:
  - name: impressions
    tolerance_pct: 1.0
    checks: [reconciliation, trend_sanity]

dimensions:
  - name: platform
    completeness_check: true
    expected_values: [LinkedIn, Instagram, Facebook]

checks:
  freshness:    { enabled: true }
  reconciliation: { enabled: true }
  parts_sum:    { enabled: true, pivot_column: platform }
  trend_sanity: { enabled: true, max_wow_change_pct: 50.0 }
  completeness: { enabled: true }
```

Commit the YAML. This is the human-approval step — the job will not run a
check that isn't in this file.

### 3. Schedule the Databricks job

Import `engine/validator.ipynb` into your Databricks workspace
(Workspace → Import → this file).

Set the **job parameters** (Databricks widgets):

| Widget | Value |
|---|---|
| `dashboard_name` | matches your YAML filename (without `.yaml`) |
| `registry_root` | absolute path to `registry/` in your Databricks Repo |
| `slack_webhook` | Slack Incoming Webhook URL (or blank to skip Slack) |
| `run_week` | blank = auto-detect from `ids_coredata.dim_date` |

Schedule it to run **before** your dashboard refresh (e.g. 06:00 UTC if
refresh is at 06:30 UTC).

### 4. Set up the Anthropic API key (for triage)

Add `ANTHROPIC_API_KEY` to the cluster's environment variables, or use a
Databricks Secret:

```python
# In validator.ipynb, replace the env var lookup in triage_agent.py:
api_key = dbutils.secrets.get(scope="my-scope", key="anthropic-api-key")
```

### 5. Run it

Trigger the job manually for the first time. You should see:

```
[validator] Registry path: .../registry/my_dashboard.yaml
[engine] Running: freshness
[engine] Running: reconciliation / impressions
...
══════════════════════════════════════════════════════════════════════
  Dashboard : my_dashboard
  Week      : 2026-23
  Result    : PASS
  Summary   : 18 PASS  ·  0 DRIFT  ·  0 FAIL
══════════════════════════════════════════════════════════════════════
```

---

## Check types

| Check | What it does | FAIL condition |
|---|---|---|
| `freshness` | Latest date in dashboard == run_week | Any mismatch |
| `reconciliation` | Dashboard SUM(metric) ≈ source SUM(metric) | Gap > tolerance_pct × 3 |
| `parts_sum` | Per-platform dashboard subtotals ≈ source subtotals | 2+ platforms out of tolerance |
| `trend_sanity` | WoW change within bounds | \|WoW%\| > max_wow_change_pct × 1.5 |
| `completeness` | All expected dimension values present | 2+ values missing |

DRIFT = approaching but not yet at the FAIL threshold. Useful for early warning.

---

## YAML registry schema

Full field reference:

```yaml
dashboard:       <string>   # display name (used in Slack, logs)
description:     <string>   # optional — for documentation

dashboard_table: <schema.table>   # the Delta table the BI tool reads
source_table:    <schema.table>   # the gold/silver source to recompute from
date_column:     <column_name>    # default: fiscal_yr_and_wk_desc
date_format:     <string>         # informational only — YYYY-WW

metrics:
  - name:          <column>       # must exist in both tables
    tolerance_pct: <float>        # max acceptable gap, e.g. 1.0 = 1%
    checks:        [reconciliation, trend_sanity]

dimensions:
  - name:                <column>
    completeness_check:  true|false
    expected_values:     [Value1, Value2, ...]

checks:
  freshness:      { enabled: true|false }
  reconciliation: { enabled: true|false }
  parts_sum:      { enabled: true|false, pivot_column: <col> }
  trend_sanity:   { enabled: true|false, max_wow_change_pct: <float> }
  completeness:   { enabled: true|false }
```

---

## Adapting `dim_date` for other projects

`validator.ipynb` resolves `run_week` using `ids_coredata.dim_date`.
If your project uses a different date dimension, change this cell:

```python
# Replace the ids_coredata.dim_date query with your own fiscal calendar
result = spark.sql("SELECT MAX(your_week_col) FROM your_schema.your_dim_date WHERE ...")
```

---

## Technologies

Python · PySpark · Databricks · YAML · Anthropic API (Claude) · Slack Webhooks
