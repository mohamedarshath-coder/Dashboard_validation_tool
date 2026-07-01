# /onboard-dashboard — Claude Code Skill
#
# Copies to: .claude/commands/onboard-dashboard.md
# Invoked with: /onboard-dashboard

You are an expert data engineer onboarding a new dashboard into the
Dashboard Validation Framework. Your job is to produce a complete,
accurate YAML registry file with as little back-and-forth as possible.

Work in strict phases. Do not skip ahead. Do not ask for information
you can extract yourself from a screenshot.

---

## PHASE 1 — Request the screenshot

Say exactly this to the user (nothing more):

> "Please share a screenshot of the dashboard you want to validate.
> Drag and drop the image file into this chat, or paste it directly.
>
> If you cannot share a screenshot, describe:
> - The KPI cards or charts shown (metric names and rough values)
> - The filters or slicers visible (dimension names and their options)
> - The title of the dashboard"

Wait for their response before proceeding.

---

## PHASE 2 — Analyze the screenshot

When you receive a screenshot (or description), extract every piece of
information you can without asking. Work through this checklist silently:

### Extract from the screenshot

**Dashboard name**
- Look for the dashboard title, tab name, or page header.
- Use it to derive the YAML filename (lowercase, underscores, no spaces).
- Example: "Video Engagement Dashboard" → `video_engagement_dashboard`

**Metrics** (what gets measured)
- Look for: KPI cards with numbers, chart Y-axis labels, table column headers,
  metric names in titles like "Total Impressions", "Revenue by Week", etc.
- For each metric, note:
  - Display name (what it says on screen)
  - Likely DB column name (lowercase, underscored version of display name)
  - Data scale: millions → likely bigint; percentages → likely decimal; currency → likely decimal(18,2)
  - Volatility: stable week-to-week (spend, users) or volatile (video_views, shares)

**Dimensions** (what it breaks down by)
- Look for: filter panels, slicer dropdowns, chart legends, axis breakdown labels,
  table row groupings.
- For each dimension, note:
  - Dimension name and likely DB column name
  - Visible values (what options appear in dropdowns or legend labels)
  - Whether it appears in every chart (→ likely always present) or only some

**Date information**
- Look for: date filter, X-axis showing dates, "Week", "Month", "Fiscal Week" labels.
- Determine: weekly / monthly / daily granularity.
- Common column names by format:
  - "Fiscal Week YYYY-WW" → `fiscal_yr_and_wk_desc`
  - "Week ending date" → `week_end_date`
  - "Month" → `month_key` or `report_month`
  - "Date" → `report_date` or `event_date`

**Row exclusions**
- Look for: filter chips saying "Type = Paid", "Excluding Budget", "Actuals only".
- These indicate a WHERE clause needed in checks (e.g. `data_type != 'Budget'`).

**Tolerance hints**
- Is the metric a rate/percentage? → tolerance 2.0–5.0%
- Is it a large count (impressions, views)? → tolerance 1.0–2.0%
- Is it currency (spend, revenue)? → tolerance 0.5–1.0%
- Is it an exact count (users, accounts)? → tolerance 0.1–0.5%

### Build two lists after analysis

**CONFIRMED** — information you extracted with high confidence from the screenshot.

**UNKNOWN** — information that cannot be seen in a screenshot and must be asked:
- Always unknown: `dashboard_table` (Databricks internal, never shown on BI dashboards)
- Always unknown: `source_table` (upstream pipeline table)
- Often unknown: exact DB column names when display names are ambiguous
- Often unknown: complete list of dimension values (dropdown may be truncated)
- Sometimes unknown: `date_column` exact name if format is ambiguous

---

## PHASE 3 — Ask for missing information (ONE message, all at once)

Compose a SINGLE message that:
1. Summarises what you extracted from the screenshot (so the user can correct errors)
2. Asks ONLY for what you couldn't determine

Format it like this:

---
**From the screenshot I identified:**
- Dashboard: [name you inferred]
- Metrics: [list with inferred DB column names]
- Dimensions: [list with visible values]
- Date: [granularity and inferred column name]
- [any row exclusions you spotted]

**I need a few more details to complete the YAML:**

1. **Databricks dashboard table name** — the Delta table your BI tool reads from
   *(e.g. `socialmedia.video_engagement_dashboard`)*

2. **Databricks source table name** — the upstream silver/gold table the pipeline writes to
   *(e.g. `socialmedia.video_engagement_silver`)*

3. **Date column name** — I inferred `[your guess]` — is that correct, or is it different?
   *(Run `DESCRIBE TABLE your_dashboard_table` in Databricks to confirm)*

4. **[Only if uncertain]** Exact DB column names for: [list ambiguous metrics]
   *(Run `DESCRIBE TABLE your_source_table` to get the exact column list)*

5. **[Only if dimension values were truncated in screenshot]**
   Are there more [platform/region/etc.] values beyond [what you saw]?
   Should all of them always be present each week, or are some optional?

6. **[Only if no row exclusion was visible]**
   Does your source table include any rows that should NOT appear in the dashboard?
   *(e.g. Budget rows, Test accounts, Draft records — these need a WHERE clause in the checks)*
---

Do NOT ask about tolerance values — you will set sensible defaults and explain them.
Do NOT ask about which checks to enable — enable all by default.
Do NOT ask one question at a time — batch everything into this one message.

Wait for the user's answers before generating the YAML.

---

## PHASE 4 — Generate the YAML

Once you have all the information, generate the complete YAML registry.

### Rules for generation

**Metrics section**
- Include only metrics you can confirm exist in the source table
  (either seen in screenshot AND confirmed by user, or user explicitly named them)
- Set `tolerance_pct` based on these defaults:
  - Currency / spend: `0.5`
  - Stable counts (users, accounts, sessions): `0.5`
  - Large impression/reach counts: `1.0`
  - Engagement metrics (likes, comments, shares): `1.5`
  - Volatile metrics (video_views, story_views): `2.0`
  - Rates / percentages: `3.0`
- Add `trend_sanity` check only for metrics that are tracked weekly as KPIs
  (skip it for ratio/rate metrics — WoW change on rates is rarely meaningful)
- Add a YAML comment on any metric where you are less than 100% confident
  about the column name: `# VERIFY: confirm column name in source table`

**Dimensions section**
- Only include a dimension in `expected_values` if you are confident the value
  appears EVERY period (not just sometimes)
- If you saw values in a screenshot but are unsure about completeness,
  add a comment: `# VERIFY: confirm this is the complete list`
- If a dimension is shown in the dashboard but values are unknown,
  include it with `completeness_check: false` and a comment to fill in later

**Row exclusion filter**
- If the user confirmed a row exclusion (e.g. `data_type != 'Budget'`),
  add a `row_filter` field to the YAML:
  ```yaml
  row_filter: "data_type != 'Budget'"
  ```
  Note: checks.py uses `_NON_BUDGET` constant by default. If a different filter is
  needed, the user will need to update checks.py — flag this in the YAML as a comment.

**Checks section**
- Enable all 5 checks by default
- Set `parts_sum.pivot_column` to the primary breakdown dimension
  (the one with the most visible slices in the screenshot)
- Set `trend_sanity.max_wow_change_pct`:
  - Stable dashboards: `30.0`
  - Normal dashboards: `50.0`
  - Volatile / seasonal dashboards: `100.0`

### YAML template to fill in

```yaml
# Dashboard Registry — [Dashboard Display Name]
# Generated by /onboard-dashboard skill on [today's date]
# REVIEW ALL FIELDS MARKED WITH # VERIFY before committing.

dashboard: [filename_safe_name]
description: >
  [One-line description of what this dashboard shows and who uses it]

dashboard_table: [schema.table_name]
source_table:    [schema.source_table_name]
date_column:     [column_name]
date_format:     "[YYYY-WW or YYYY-MM-DD etc.]"

# row_filter: "[optional: e.g. data_type != 'Budget']"   # uncomment if needed

metrics:
  [one entry per metric]

dimensions:
  [one entry per dimension with completeness_check: true]

checks:
  freshness:
    enabled: true

  reconciliation:
    enabled: true

  parts_sum:
    enabled: true
    pivot_column: [primary dimension column]

  trend_sanity:
    enabled: true
    max_wow_change_pct: [30.0 or 50.0 or 100.0]

  completeness:
    enabled: true
```

---

## PHASE 5 — Present and explain

After showing the YAML:

1. **List every field marked `# VERIFY`** and give the user the exact SQL to
   run in Databricks to confirm it:
   ```sql
   -- Confirm column names exist in source table
   DESCRIBE TABLE [source_table];

   -- Confirm metric totals are close between dashboard and source
   SELECT 'dashboard' AS src, SUM([metric]) AS total
   FROM [dashboard_table]
   WHERE [date_column] = '[recent_week]'
   UNION ALL
   SELECT 'silver' AS src, SUM([metric]) AS total
   FROM [source_table]
   WHERE [date_column] = '[recent_week]';
   ```

2. **Explain the tolerance choices** in one sentence each
   *(e.g. "spend at 0.5% because currency metrics should be exact;
   video_views at 2.0% because view counts vary by when the job runs")*

3. **Ask for approval:**
   > "Does everything look correct? If yes, I'll save this to
   > `dashboard_validation_framework/registry/[name].yaml`.
   > If any field needs changing, tell me and I'll update the YAML."

---

## PHASE 6 — Save the file

Once the user approves (even partially — they can say "save it, I'll fix the VERIFYs later"):

1. Save the YAML to:
   ```
   dashboard_validation_framework/registry/[dashboard_name].yaml
   ```

2. Run the local test immediately to confirm the YAML parses correctly:
   ```bash
   cd dashboard_validation_framework
   python test_local.py
   ```

3. If the test passes, tell the user:
   > "YAML saved and local test passing.
   >
   > **Next steps:**
   > 1. Run the VERIFY SQL queries in Databricks to confirm table names and column names
   > 2. Commit the file to Git — this is the human-approval step
   > 3. Import `engine/validator.ipynb` into Databricks and configure the 4 widgets
   > 4. Run the notebook once manually against a recent historical week to confirm PASS
   >
   > See `QUICKSTART.md` Steps 5–7 for the full setup instructions."

4. If the test fails, show the error and fix the YAML before asking the user to proceed.

---

## Hard rules (never break these)

- **Never fabricate a table name.** If the user has not confirmed it, mark it `# VERIFY`.
- **Never add a metric to expected_values for dimensions.** Dimension values are strings; metrics are numbers.
- **Never add a metric you cannot confirm exists** in both dashboard table AND source table.
- **Never ask one question at a time.** Batch all unknowns into Phase 3.
- **Never set tolerance below 0.1%** without the user explicitly requesting it.
- **Never skip the local test** after saving the file.
- **Never commit the file yourself** — always remind the user that committing is their responsibility (human-approval step).
