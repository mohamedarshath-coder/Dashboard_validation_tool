# Dashboard Validation Framework

This folder is a self-contained tool that validates Power BI / Databricks dashboards
automatically before every refresh. It catches data quality issues before stakeholders see them.

## What this tool does

1. A YAML file (registry/) defines what to check for a dashboard — metrics, dimensions, tolerances.
2. A Databricks job (engine/validator.ipynb) reads the YAML and runs SQL checks every week.
3. Results are posted to Slack: PASS = quiet green line, FAIL = full breakdown.
4. On FAIL, Claude Haiku explains the root cause via the Anthropic API.

## Folder structure

```
dashboard_validation_framework/
├── registry/          ← one YAML file per dashboard (human-approved config)
├── engine/
│   ├── checks.py      ← 5 check types: freshness, reconciliation, parts_sum, trend_sanity, completeness
│   ├── validation_engine.py  ← reads YAML, runs all checks
│   ├── reporter.py    ← Slack Block Kit + console output
│   └── validator.ipynb       ← Databricks entry point, schedule this
├── triage/
│   └── triage_agent.py       ← calls Claude Haiku on FAIL to explain why
├── onboarding/
│   ├── ONBOARD_NEW_DASHBOARD.md  ← step-by-step guide
│   └── onboard-dashboard.md      ← /onboard-dashboard Claude Code skill
├── QUICKSTART.md      ← one-page guide for new teams
├── README.md          ← full reference
├── test_local.py      ← run this first: python test_local.py (no Spark needed)
└── requirements.txt   ← pyyaml, anthropic, requests
```

## Key design rule

Claude touches the dashboard ONCE at setup (screenshot → YAML). After that, every weekly
validation run is pure deterministic SQL — no AI in the verdict.

## Pilot dashboard

`registry/social_hub_consolidated_dashboard.yaml` is the working example for the
social_hub project. Table names: `socialmedia.social_hub_consolidated_dashboard` (dashboard)
and `socialmedia.social_media_consolidated_silver` (source). Date column: `fiscal_yr_and_wk_desc` (YYYY-WW).

## To onboard a new dashboard

Run `/onboard-dashboard` in Claude Code. The skill will:
1. Ask for a dashboard screenshot
2. Extract metrics and dimensions from the screenshot
3. Ask only for what can't be seen (table names, column names)
4. Generate the YAML, save it to registry/, run test_local.py

## To run the local test (no Databricks needed)

```bash
cd dashboard_validation_framework
python test_local.py
```

## Dependencies

```bash
pip install pyyaml anthropic requests
```

PySpark is provided by Databricks — not listed in requirements.txt.
