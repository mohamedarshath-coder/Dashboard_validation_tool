"""
ValidationEngine — reads a YAML registry and runs all configured checks.

Supports two YAML schemas (both work side-by-side):

NEW (deck-style):
    source_tables: [gold.table_a, gold.table_b]
    metrics:
      - name: play_rate
        recompute_sql: "SELECT SUM(plays)/SUM(impressions) FROM ... WHERE event_date='{run_week}'"
        dimensions: [region, content_type]
        tolerance: 0.5%
        checks: [freshness, reconciliation, parts_sum, trend, completeness]

OLD (backward-compatible):
    source_table: socialmedia.silver
    metrics:
      - name: impressions
        tolerance_pct: 1.0
        checks: [reconciliation, trend_sanity]
    checks:
      parts_sum:
        pivot_column: platform
"""

import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, List

from checks import (
    CheckResult, Status,
    run_freshness_check,
    run_reconciliation_check,
    run_parts_sum_check,
    run_trend_sanity_check,
    run_completeness_check,
)


class ValidationEngine:

    def __init__(self, spark, registry_path: str):
        self.spark = spark
        self.registry_path = str(registry_path)
        path = Path(registry_path)
        if not path.exists():
            raise FileNotFoundError(f"Registry not found: {registry_path}")
        with open(path, "r", encoding="utf-8") as f:
            self.registry = yaml.safe_load(f)

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _prev_week(run_week: str, date_format: str = "YYYY-WW") -> str:
        """Return the period immediately before run_week."""
        if date_format == "YYYY-MM-DD":
            from datetime import datetime, timedelta
            d = datetime.strptime(run_week, "%Y-%m-%d")
            return (d - timedelta(days=7)).strftime("%Y-%m-%d")
        year, week = map(int, run_week.split("-"))
        if week <= 1:
            return f"{year - 1}-52"
        return f"{year}-{week - 1:02d}"

    @staticmethod
    def _parse_tolerance(metric_cfg: dict) -> float:
        """Support both 'tolerance: 0.5%' and 'tolerance_pct: 0.5'."""
        raw = metric_cfg.get("tolerance", metric_cfg.get("tolerance_pct", "1.0"))
        return float(str(raw).rstrip("%"))

    @staticmethod
    def _parse_checks(metric_cfg: dict) -> List[str]:
        """Normalize check names: 'trend' → 'trend_sanity'."""
        raw = metric_cfg.get("checks", ["reconciliation"])
        return ["trend_sanity" if c == "trend" else c for c in raw]

    # ── main entry point ──────────────────────────────────────────────────────

    def run(self, run_week: str) -> Dict:
        reg = self.registry
        dashboard_table = reg["dashboard_table"]

        # Support source_tables (list) or source_table (string)
        source_tables_list = reg.get("source_tables") or []
        if not source_tables_list:
            singular = reg.get("source_table", "")
            source_tables_list = [singular] if singular else []
        default_source_table = source_tables_list[0] if source_tables_list else ""

        date_column  = reg.get("date_column", "fiscal_yr_and_wk_desc")
        date_format  = reg.get("date_format", "YYYY-WW").strip('"')
        row_filter   = reg.get("row_filter", "")
        checks_cfg   = reg.get("checks", {})
        prev_week    = self._prev_week(run_week, date_format)

        # Dimension lookup for per-metric completeness
        dim_lookup = {d["name"]: d for d in reg.get("dimensions", [])}

        results: List[CheckResult] = []

        # ── Global freshness (runs unless a metric owns it) ───────────────────
        metric_owns_freshness = any(
            "freshness" in self._parse_checks(m)
            for m in reg.get("metrics", [])
        )
        if not metric_owns_freshness and checks_cfg.get("freshness", {}).get("enabled", True):
            print(f"[engine] Running: freshness")
            results.append(
                run_freshness_check(self.spark, dashboard_table, run_week, date_column, row_filter)
            )

        # ── Per-metric checks ─────────────────────────────────────────────────
        for metric_cfg in reg.get("metrics", []):
            metric        = metric_cfg["name"]
            tolerance_pct = self._parse_tolerance(metric_cfg)
            check_types   = self._parse_checks(metric_cfg)
            recompute_sql = metric_cfg.get("recompute_sql", "")
            metric_source = metric_cfg.get("source_table", default_source_table)
            metric_dims   = metric_cfg.get("dimensions", [])  # new-style per-metric dims

            # Freshness (per-metric)
            if "freshness" in check_types:
                print(f"[engine] Running: freshness / {metric}")
                results.append(
                    run_freshness_check(self.spark, dashboard_table, run_week, date_column, row_filter)
                )

            # Reconciliation — runs if listed OR if recompute_sql is provided
            run_recon = (
                ("reconciliation" in check_types or bool(recompute_sql))
                and checks_cfg.get("reconciliation", {}).get("enabled", True)
            )
            if run_recon:
                print(f"[engine] Running: reconciliation / {metric}")
                results.append(
                    run_reconciliation_check(
                        self.spark, dashboard_table, metric_source,
                        metric, run_week, tolerance_pct, date_column, row_filter,
                        recompute_sql,
                    )
                )

            # Trend sanity
            if "trend_sanity" in check_types and checks_cfg.get("trend_sanity", {}).get("enabled", True):
                max_wow = float(checks_cfg.get("trend_sanity", {}).get("max_wow_change_pct", 50.0))
                print(f"[engine] Running: trend_sanity / {metric}")
                results.append(
                    run_trend_sanity_check(
                        self.spark, dashboard_table,
                        metric, run_week, prev_week, max_wow, date_column, row_filter,
                    )
                )

            # Parts sum — per-metric dimensions (new) or global pivot_column (old)
            if "parts_sum" in check_types and checks_cfg.get("parts_sum", {}).get("enabled", True):
                dims_for_parts = metric_dims or [checks_cfg.get("parts_sum", {}).get("pivot_column", "platform")]
                for dim_name in dims_for_parts:
                    print(f"[engine] Running: parts_sum / {metric} by {dim_name}")
                    results.append(
                        run_parts_sum_check(
                            self.spark, dashboard_table, metric_source,
                            metric, run_week, dim_name, tolerance_pct, date_column, row_filter,
                        )
                    )

            # Completeness — per-metric dimensions (new style)
            if "completeness" in check_types and checks_cfg.get("completeness", {}).get("enabled", True):
                for dim_name in metric_dims:
                    dim_cfg = dim_lookup.get(dim_name, {})
                    expected = dim_cfg.get("expected_values", [])
                    if expected:
                        print(f"[engine] Running: completeness / {dim_name}")
                        results.append(
                            run_completeness_check(
                                self.spark, dashboard_table,
                                dim_name, expected,
                                run_week, date_column, row_filter,
                            )
                        )

        # ── Global parts_sum + completeness (old-style YAMLs only) ───────────
        # Skip if any metric already used per-metric dimensions (new-style)
        any_metric_has_dims = any(m.get("dimensions") for m in reg.get("metrics", []))

        if not any_metric_has_dims:
            if checks_cfg.get("parts_sum", {}).get("enabled", True):
                pivot_col = checks_cfg.get("parts_sum", {}).get("pivot_column", "platform")
                for metric_cfg in reg.get("metrics", []):
                    if "reconciliation" in self._parse_checks(metric_cfg):
                        metric        = metric_cfg["name"]
                        tolerance_pct = self._parse_tolerance(metric_cfg)
                        metric_source = metric_cfg.get("source_table", default_source_table)
                        print(f"[engine] Running: parts_sum / {metric} by {pivot_col}")
                        results.append(
                            run_parts_sum_check(
                                self.spark, dashboard_table, metric_source,
                                metric, run_week, pivot_col, tolerance_pct, date_column, row_filter,
                            )
                        )

            if checks_cfg.get("completeness", {}).get("enabled", True):
                for dim_cfg in reg.get("dimensions", []):
                    if dim_cfg.get("completeness_check", False):
                        print(f"[engine] Running: completeness / {dim_cfg['name']}")
                        results.append(
                            run_completeness_check(
                                self.spark, dashboard_table,
                                dim_cfg["name"], dim_cfg.get("expected_values", []),
                                run_week, date_column, row_filter,
                            )
                        )

        # ── Aggregate ─────────────────────────────────────────────────────────
        n_fail  = sum(1 for r in results if r.status == Status.FAIL)
        n_drift = sum(1 for r in results if r.status == Status.DRIFT)
        n_pass  = sum(1 for r in results if r.status == Status.PASS)

        overall = Status.FAIL if n_fail > 0 else (Status.DRIFT if n_drift > 0 else Status.PASS)

        return {
            "dashboard":      reg["dashboard"],
            "run_week":       run_week,
            "run_timestamp":  datetime.utcnow().isoformat(timespec="seconds"),
            "registry_path":  self.registry_path,
            "summary":        {"total": len(results), "pass": n_pass, "drift": n_drift, "fail": n_fail},
            "results":        results,
            "overall_status": overall,
        }
