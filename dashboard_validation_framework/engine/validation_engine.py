"""
ValidationEngine — reads a YAML registry and runs all configured checks.

Usage (Databricks notebook):
    engine = ValidationEngine(spark, "/path/to/registry.yaml")
    result = engine.run(run_week="2026-23")
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
        path = Path(registry_path)
        if not path.exists():
            raise FileNotFoundError(f"Registry not found: {registry_path}")
        with open(path, "r", encoding="utf-8") as f:
            self.registry = yaml.safe_load(f)

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _prev_week(run_week: str) -> str:
        """Return the fiscal week immediately before run_week (YYYY-WW format).

        For week 01, rolls back to week 52 of the previous year. This is an
        approximation — some years have 53 weeks. For edge cases the trend
        check will find no data and skip gracefully.
        """
        year, week = map(int, run_week.split("-"))
        if week <= 1:
            return f"{year - 1}-52"
        return f"{year}-{week - 1:02d}"

    # ── main entry point ──────────────────────────────────────────────────────

    def run(self, run_week: str) -> Dict:
        """Run all configured checks and return a structured result dict.

        Parameters
        ----------
        run_week : str
            Fiscal week to validate in YYYY-WW format, e.g. "2026-23".

        Returns
        -------
        dict with keys: dashboard, run_week, run_timestamp, summary,
                        results (List[CheckResult]), overall_status
        """
        reg = self.registry
        dashboard_table = reg["dashboard_table"]
        source_table    = reg["source_table"]
        date_column     = reg.get("date_column", "fiscal_yr_and_wk_desc")
        checks_cfg      = reg.get("checks", {})
        prev_week       = self._prev_week(run_week)

        results: List[CheckResult] = []

        # 1 — Freshness
        if checks_cfg.get("freshness", {}).get("enabled", True):
            print(f"[engine] Running: freshness")
            results.append(
                run_freshness_check(self.spark, dashboard_table, run_week, date_column)
            )

        # 2 — Per-metric: reconciliation + trend_sanity
        for metric_cfg in reg.get("metrics", []):
            metric        = metric_cfg["name"]
            tolerance_pct = float(str(metric_cfg.get("tolerance_pct", 1.0)).rstrip("%"))
            check_types   = metric_cfg.get("checks", ["reconciliation"])

            if "reconciliation" in check_types and checks_cfg.get("reconciliation", {}).get("enabled", True):
                print(f"[engine] Running: reconciliation / {metric}")
                results.append(
                    run_reconciliation_check(
                        self.spark, dashboard_table, source_table,
                        metric, run_week, tolerance_pct, date_column,
                    )
                )

            if "trend_sanity" in check_types and checks_cfg.get("trend_sanity", {}).get("enabled", True):
                max_wow = float(checks_cfg.get("trend_sanity", {}).get("max_wow_change_pct", 50.0))
                print(f"[engine] Running: trend_sanity / {metric}")
                results.append(
                    run_trend_sanity_check(
                        self.spark, dashboard_table,
                        metric, run_week, prev_week, max_wow, date_column,
                    )
                )

        # 3 — Parts-sum (platform breakdown for every reconciled metric)
        if checks_cfg.get("parts_sum", {}).get("enabled", True):
            pivot_col = checks_cfg.get("parts_sum", {}).get("pivot_column", "platform")
            for metric_cfg in reg.get("metrics", []):
                if "reconciliation" in metric_cfg.get("checks", []):
                    metric        = metric_cfg["name"]
                    tolerance_pct = float(str(metric_cfg.get("tolerance_pct", 1.0)).rstrip("%"))
                    print(f"[engine] Running: parts_sum / {metric} by {pivot_col}")
                    results.append(
                        run_parts_sum_check(
                            self.spark, dashboard_table, source_table,
                            metric, run_week, pivot_col, tolerance_pct, date_column,
                        )
                    )

        # 4 — Completeness (one check per dimension with completeness_check: true)
        if checks_cfg.get("completeness", {}).get("enabled", True):
            for dim_cfg in reg.get("dimensions", []):
                if dim_cfg.get("completeness_check", False):
                    print(f"[engine] Running: completeness / {dim_cfg['name']}")
                    results.append(
                        run_completeness_check(
                            self.spark, dashboard_table,
                            dim_cfg["name"], dim_cfg.get("expected_values", []),
                            run_week, date_column,
                        )
                    )

        # ── aggregate ────────────────────────────────────────────────────────
        n_fail  = sum(1 for r in results if r.status == Status.FAIL)
        n_drift = sum(1 for r in results if r.status == Status.DRIFT)
        n_pass  = sum(1 for r in results if r.status == Status.PASS)

        if n_fail > 0:
            overall = Status.FAIL
        elif n_drift > 0:
            overall = Status.DRIFT
        else:
            overall = Status.PASS

        return {
            "dashboard":       reg["dashboard"],
            "run_week":        run_week,
            "run_timestamp":   datetime.utcnow().isoformat(timespec="seconds"),
            "registry_path":   str(self.registry),
            "summary":         {"total": len(results), "pass": n_pass, "drift": n_drift, "fail": n_fail},
            "results":         results,
            "overall_status":  overall,
        }
