"""Diff tool for comparing two evaluation reports.

Usage:
    python -m trading_bot.diff_reports report_v1.json report_v2.json
    python -m trading_bot.diff_reports report_v1.json report_v2.json --json
    python -m trading_bot.diff_reports report_v1.json report_v2.json --thresholds gate.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional


# Metrics to show in a side-by-side diff.
_DIFF_METRICS = [
    "total_pnl",
    "total_return_pct",
    "n_steps",
    "n_fills",
    "n_blocked_orders",
    "block_rate",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "cagr",
    "max_drawdown_pct",
    "max_drawdown_duration_steps",
    "profit_factor",
    "win_rate",
    "avg_win",
    "avg_loss",
    "expectancy",
    "max_consecutive_losses",
]

_REGIME_FIELDS = ["total_pnl", "steps", "fills", "blocked_orders"]


def _fmt(v: Any) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:+.4f}"
    return str(v)


def _delta_str(v1: Any, v2: Any) -> str:
    """Return a delta string: +0.5, N/A, or empty if no change."""
    if v1 is None or v2 is None:
        if v1 == v2:
            return "—"
        return "N/A ↔ N/A"
    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        d = v2 - v1
        if abs(d) < 1e-12:
            return "—"
        return f"{d:+.4f}"
    if v1 != v2:
        return f"{v1} → {v2}"
    return "—"


def diff_reports(report1: dict, report2: dict) -> dict:
    """Compute side-by-side diff between two evaluation reports.

    Returns a dict with section-by-section deltas suitable for
    JSON output or human-readable formatting.
    """
    s1 = report1.get("summary", {})
    s2 = report2.get("summary", {})
    m1 = report1.get("meta", {})
    m2 = report2.get("meta", {})
    c1 = report1.get("checks", {})
    c2 = report2.get("checks", {})

    # Summary metric diffs.
    metric_diffs: list[dict] = []
    for metric in _DIFF_METRICS:
        v1 = s1.get(metric)
        v2 = s2.get(metric)
        metric_diffs.append({
            "metric": metric,
            "v1": v1,
            "v2": v2,
            "delta": _delta_str(v1, v2),
        })

    # Regime breakdown diffs.
    reg1 = report1.get("regimes", {}).get("performance", {})
    reg2 = report2.get("regimes", {}).get("performance", {})
    regime_diffs: dict[str, list[dict]] = {}
    all_regimes = sorted(set(list(reg1.keys()) + list(reg2.keys())))
    for regime in all_regimes:
        r1 = reg1.get(regime, {})
        r2 = reg2.get(regime, {})
        diffs: list[dict] = []
        for field in _REGIME_FIELDS:
            v1 = r1.get(field)
            v2 = r2.get(field)
            diffs.append({
                "field": field,
                "v1": v1,
                "v2": v2,
                "delta": _delta_str(v1, v2),
            })
        regime_diffs[regime] = diffs

    # Check diffs.
    c1_summary = c1.get("summary", {})
    c2_summary = c2.get("summary", {})
    check_diffs = {
        "v1": {"passed": c1_summary.get("passed"), "total": c1_summary.get("total")},
        "v2": {"passed": c2_summary.get("passed"), "total": c2_summary.get("total")},
    }

    # Meta.
    meta_diff = {
        "v1_strategy": m1.get("strategy", {}).get("name", "N/A"),
        "v2_strategy": m2.get("strategy", {}).get("name", "N/A"),
        "v1_generated": m1.get("generated_at", "N/A"),
        "v2_generated": m2.get("generated_at", "N/A"),
    }

    return {
        "meta": meta_diff,
        "summary": metric_diffs,
        "regimes": regime_diffs,
        "checks": check_diffs,
    }


def format_diff(diff: dict) -> str:
    """Format a diff dict as a human-readable string."""
    lines: list[str] = []
    lines.append(f"\n{'═' * 70}")
    lines.append("  REPORT DIFF")
    lines.append(f"{'═' * 70}")

    m = diff["meta"]
    lines.append(f"  V1: {m['v1_strategy']} @ {m['v1_generated']}")
    lines.append(f"  V2: {m['v2_strategy']} @ {m['v2_generated']}")

    # Summary metrics.
    lines.append(f"\n{'─' * 70}")
    lines.append(f"  {'Metric':<28s} {'V1':>12s} {'V2':>12s} {'Δ':>12s}")
    lines.append(f"  {'─' * 28} {'─' * 12} {'─' * 12} {'─' * 12}")
    for d in diff["summary"]:
        delta = d["delta"]
        marker = ""
        if delta and delta != "—" and not delta.startswith("N/A"):
            marker = " ◀" if delta.startswith("-") else ""
        lines.append(
            f"  {d['metric']:<28s} {_fmt(d['v1']):>12s} {_fmt(d['v2']):>12s} "
            f"{delta:>12s}{marker}"
        )

    # Regime diffs.
    if diff.get("regimes"):
        lines.append(f"\n{'─' * 70}")
        lines.append("  REGIME BREAKDOWN")
        for regime, diffs in sorted(diff["regimes"].items()):
            lines.append(f"  [{regime}]")
            for d in diffs:
                lines.append(
                    f"    {d['field']:<24s} {_fmt(d['v1']):>12s} {_fmt(d['v2']):>12s} "
                    f"{d['delta']:>12s}"
                )

    # Check diffs.
    c = diff["checks"]
    lines.append(f"\n{'─' * 70}")
    lines.append(f"  CHECKS: V1={c['v1']['passed']}/{c['v1']['total']}  "
                 f"V2={c['v2']['passed']}/{c['v2']['total']}")

    lines.append(f"{'═' * 70}\n")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Diff two trading bot evaluation reports")
    p.add_argument("report1", help="Path to first evaluation_report.json")
    p.add_argument("report2", help="Path to second evaluation_report.json")
    p.add_argument("--json", action="store_true", help="Output diff as JSON")
    args = p.parse_args(argv)

    for path in [args.report1, args.report2]:
        if not os.path.exists(path):
            print(f"Error: report file not found: {path}", file=sys.stderr)
            return 1

    with open(args.report1, encoding="utf-8") as f:
        report1 = json.load(f)
    with open(args.report2, encoding="utf-8") as f:
        report2 = json.load(f)

    diff = diff_reports(report1, report2)

    if args.json:
        json.dump(diff, sys.stdout, indent=2)
        return 0

    print(format_diff(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
