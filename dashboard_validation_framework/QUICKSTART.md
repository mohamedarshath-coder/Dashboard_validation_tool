# Quickstart — New Project Setup

Get a dashboard validated in under 90 minutes.
You only ever change **one file**: the YAML registry for your dashboard.
All Python files and the Databricks notebook stay identical.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Databricks workspace | Must have read access to the dashboard table and its source/silver table |
| Python 3.8+ (laptop) | For the local test only |
| Slack Incoming Webhook URL | Create one at: Slack → Apps → Incoming Webhooks |
| Anthropic API key | From console.anthropic.com — used only when a check fails |
| Claude Code (optional) | Speeds up YAML creation via `/onboard-dashboard` skill |

---

## Step 1 — Copy the folder (2 min)

Drop `dashboard_validation_framework/` into the root of your project repo.
No configuration needed at this stage.

---

## Step 2 — Find these four facts about your dashboard (15 min)

Look in your pipeline code or Databricks catalog:

```
Dashboard table  : the Delta table your BI tool reads from
                   e.g.  myschema.my_dashboard

Source table     : the upstream silver/gold table the pipeline writes to
                   e.g.  myschema.my_silver_table

Date column      : the column used to filter by week/date
                   e.g.  fiscal_yr_and_wk_desc  or  report_date  or  event_week

Metric columns   : numeric KPIs shown on the dashboard
                   e.g.  impressions, revenue, clicks, conversions
```

---

## Step 3 — Create your YAML registry (15–60 min)

### Option A — Claude Code skill (15 min, recommended)

```bash
# Copy the skill into your project
cp dashboard_validation_framework/onboarding/onboard-dashboard.md \
   .claude/commands/onboard-dashboard.md

# In a Claude Code session:
/onboard-dashboard
```

Claude will ask for a screenshot of your dashboard and generate the YAML.
Review it carefully, then save it.

### Option B — Copy and edit the template (30 min)

```bash
cp dashboard_validation_framework/registry/social_hub_consolidated_dashboard.yaml \
   dashboard_validation_framework/registry/<your_dashboard_name>.yaml
```

Open the new file and change **only these parts**:

```yaml
# ── Change these ──────────────────────────────────────────────────
dashboard:       your_dashboard_name
dashboard_table: myschema.my_dashboard_table
source_table:    myschema.my_silver_table
date_column:     your_date_column_name      # e.g. report_date

metrics:
  - name: impressions          # replace with your actual metric column names
    tolerance_pct: 1.0         # 1% gap allowed — tighten after first few runs
    checks: [reconciliation, trend_sanity]

  - name: revenue
    tolerance_pct: 1.0
    checks: [reconciliation, trend_sanity]

dimensions:
  - name: platform             # replace with your breakdown dimension column
    completeness_check: true
    expected_values:           # list values that MUST appear every period
      - LinkedIn
      - Instagram
      - Facebook
# ── Leave everything else as-is ──────────────────────────────────
```

> **Rule of thumb for tolerances:** Start at `1.0%` for all metrics.
> After 2–3 weeks of PASS results, tighten if you want more sensitivity.

Commit the file to Git. This is the human-approval step.

---

## Step 4 — Run the local test (2 min)

```bash
cd dashboard_validation_framework
pip install pyyaml anthropic requests   # one-time
python test_local.py
```

All 9 tests should pass. If the YAML has a typo or a missing key, this
catches it before you touch Databricks.

---

## Step 5 — Verify the SQL in Databricks (15 min)

Paste this into a Databricks notebook and run it:

```python
# Replace with a recent week you know had clean data
week    = "2026-26"
dash    = "myschema.my_dashboard_table"
silver  = "myschema.my_silver_table"
date_col = "your_date_column_name"
metric   = "impressions"          # any metric from your YAML

spark.sql(f"""
  SELECT 'dashboard' AS source, COALESCE(SUM({metric}), 0) AS total
  FROM {dash}
  WHERE {date_col} = '{week}'
  UNION ALL
  SELECT 'silver' AS source, COALESCE(SUM({metric}), 0) AS total
  FROM {silver}
  WHERE {date_col} = '{week}'
""").show()
```

**What to expect:** Both numbers should be within 1% of each other.
If they differ by more, investigate the pipeline before scheduling the job.

---

## Step 6 — Configure the Databricks job (15 min)

1. Import `engine/validator.ipynb` into your Databricks workspace
   *(Workspace → Import, or sync via Databricks Repos)*

2. Set the four notebook widgets:

   | Widget | Value |
   |---|---|
   | `dashboard_name` | Your YAML filename without `.yaml` |
   | `registry_root` | Absolute Databricks path to `registry/` folder |
   | `slack_webhook` | Your Slack Incoming Webhook URL |
   | `run_week` | Leave blank — auto-detects from `ids_coredata.dim_date` * |

   > *If your project uses a different date dimension, edit cell 3 of the notebook
   > to query your own dim table instead of `ids_coredata.dim_date`.*

3. Add the Anthropic key to your cluster:
   *(Cluster → Edit → Advanced → Environment variables)*
   ```
   ANTHROPIC_API_KEY = sk-ant-...
   ```

4. Create a scheduled job to run this notebook **before** your dashboard refresh.
   *(e.g. if refresh runs at 07:00 UTC, schedule validation at 06:30 UTC)*

---

## Step 7 — First run (10 min)

**Manual run:** Trigger the job once to confirm it works.
You should see PASS in the notebook output.

**Controlled FAIL test:** Confirm the alert path works end-to-end:
1. Set `tolerance_pct: 0.0001` for one metric in your YAML
2. Run the job → Slack alert should fire with Claude's analysis
3. Revert the tolerance back to `1.0` and commit

---

## What the output looks like

**On a clean run (Slack):**
```
✓  your_dashboard_name — 18/18 checks PASS — Pre-refresh check · 2026-07-01 06:30
```

**On a failure (Slack):**
```
✗  Dashboard validation — your_dashboard_name
   Pre-refresh check · 2026-07-01 06:30  |  1 FAIL  0 DRIFT  17 PASS

   impressions   FAIL   severity P2
   Expected: 42,000,000   Got: 35,700,000   Gap: -6,300,000   Tolerance: ±1%

   Claude's analysis: The APAC partition is missing from last night's load...
   Suggested action: re-run ingestion for APAC, then re-validate before publishing.
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `test_local.py` fails with `FileNotFoundError` | Check that your YAML filename matches `dashboard_name` exactly |
| `ModuleNotFoundError: checks` in Databricks | Set `registry_root` to the correct absolute path — `framework_root` is derived from it |
| `run_week` stays blank | Your workspace may not have `ids_coredata.dim_date` — edit cell 3 of `validator.ipynb` to use your own dim table |
| Slack not receiving messages | Verify the webhook URL is correct and the Slack app has permission to post to that channel |
| Claude triage missing | Confirm `ANTHROPIC_API_KEY` is set on the cluster (not just locally) |
| Reconciliation FAIL on first run | Run the Phase 5 SQL manually — the gap may reveal a real pipeline issue worth investigating |

---

## Files you will touch

```
dashboard_validation_framework/
    registry/
        your_dashboard_name.yaml    ← CREATE THIS (copy from social_hub example)

engine/validator.ipynb              ← EDIT cell 3 only if you use a custom dim_date table
```

Everything else stays identical.

---

## Need help?

- Full YAML schema reference → [README.md](README.md)
- Detailed onboarding steps → [onboarding/ONBOARD_NEW_DASHBOARD.md](onboarding/ONBOARD_NEW_DASHBOARD.md)
- AI-assisted YAML creation → [onboarding/onboard-dashboard.md](onboarding/onboard-dashboard.md) (Claude Code skill)
