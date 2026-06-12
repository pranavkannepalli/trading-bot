"""CLI to read and summarize an evaluation report.

Usage:
    python -m trading_bot.evaluate --report artifacts/evaluation_report.json
    python -m trading_bot.evaluate --report artifacts/evaluation_report.json --checks-only
    python -m trading_bot.evaluate --report artifacts/evaluation_report.json --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional


def _format_pct(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:+.2f}%"


def _format_float(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v:+,.{decimals}f}"


def _format_ratio(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.3f}"


def _separator(title: str) -> str:
    return f"\n{'─' * 50}\n  {title}\n{'─' * 50}"


def summarize_report(report: dict) -> str:
    """Return a human-readable summary string for an evaluation report."""
    s = report["summary"]
    m = report["meta"]
    r = report.get("risk", {})
    reg = report.get("regimes", {})
    c = report.get("counsel")
    chk = report.get("checks", {})

    lines: list[str] = []
    lines.append(_separator("TRADING BOT — EVALUATION REPORT"))
    lines.append(f"  Run:      {m['generated_at']}")
    lines.append(f"  Strategy: {m['strategy']['name']} ({m['strategy']['class']})")
    lines.append(f"  Version:  {m['report_version']}")

    # --- Summary ---
    lines.append(_separator("PERFORMANCE SUMMARY"))
    lines.append(f"  Total P&L:       {_format_float(s['total_pnl'])}")
    lines.append(f"  Total Return:    {_format_pct(s.get('total_return_pct'))}")
    lines.append(f"  Steps:           {s['n_steps']}")
    lines.append(f"  Fills:           {s['n_fills']}")
    lines.append(f"  Blocked Orders:  {s['n_blocked_orders']} / {s['n_proposed_orders']} ({s['block_rate']:.1%})")

    # --- Risk-Adjusted ---
    lines.append(_separator("RISK-ADJUSTED METRICS"))
    lines.append(f"  Sharpe Ratio:    {_format_ratio(s.get('sharpe_ratio'))}")
    lines.append(f"  Sortino Ratio:   {_format_ratio(s.get('sortino_ratio'))}")
    lines.append(f"  Calmar Ratio:    {_format_ratio(s.get('calmar_ratio'))}")
    lines.append(f"  CAGR:            {_format_pct(s.get('cagr'))}")
    lines.append(f"  Max Drawdown:    {_format_pct(s.get('max_drawdown_pct'))}")
    lines.append(f"  DD Duration:     {s.get('max_drawdown_duration_steps', 'N/A')} steps")

    # --- Trade Metrics ---
    lines.append(_separator("TRADE METRICS"))
    lines.append(f"  Profit Factor:   {_format_ratio(s.get('profit_factor'))}")
    lines.append(f"  Win Rate:        {_format_pct(s.get('win_rate'))}")
    lines.append(f"  Avg Win:         {_format_float(s.get('avg_win'))}")
    lines.append(f"  Avg Loss:        {_format_float(s.get('avg_loss'))}")
    lines.append(f"  Expectancy:      {_format_float(s.get('expectancy'))}")
    lines.append(f"  Max Consec Loss: {s.get('max_consecutive_losses', 'N/A')}")

    # --- Regime Breakdown ---
    lines.append(_separator("REGIME BREAKDOWN"))
    perf = reg.get("performance", {})
    dist = reg.get("distribution", {})
    for regime in ["bull", "bear", "range", "volatile"]:
        if regime in perf:
            p = perf[regime]
            d = dist.get(regime, {})
            lines.append(
                f"  {regime:10s}  steps={p['steps']:3d}  fills={p['fills']:3d}  "
                f"P&L={_format_float(p['total_pnl'])}  "
                f"blocked={p['blocked_orders']:3d}  "
                f"type={p['option_type_used']}"
            )
    if reg.get("transitions"):
        lines.append(f"  Transitions: {json.dumps(reg['transitions'])}")

    # --- Drawdown ---
    dd = r.get("drawdown", {})
    lines.append(_separator("DRAWDOWN"))
    lines.append(f"  Max DD:         {_format_pct(dd.get('max_drawdown_pct'))}")
    lines.append(f"  Max DD Abs:     {_format_float(dd.get('max_drawdown_absolute'))}")
    lines.append(f"  Peak Step:      {dd.get('peak_step', 'N/A')}")
    lines.append(f"  Trough Step:    {dd.get('trough_step', 'N/A')}")
    lines.append(f"  Recovery Step:  {dd.get('recovery_step', 'N/A')}")

    # --- Counsel ---
    if c:
        lines.append(_separator("COUNSEL AUDIT"))
        lines.append(f"  Type:           {c['counsel_type']}")
        lines.append(f"  Total Calls:    {c['total_calls']}")
        caps = c.get("caps_applied", {})
        lines.append(f"  Delta caps hit: {caps.get('delta_cap_hit_count', 0)}")
        lines.append(f"  Vega caps hit:  {caps.get('vega_cap_hit_count', 0)}")
        lines.append(f"  Qty clamps:     {caps.get('quantity_clamp_count', 0)}")

    # --- Goss ---
    lines.append(_separator("GREEKS EXPOSURE"))
    ge = r.get("greeks_exposure", {})
    ex = ge.get("extremes", {})
    lines.append(f"  Max |Δ|:        {_format_float(ex.get('max_abs_delta'), 4)}")
    lines.append(f"  Max |Vega|:     {_format_float(ex.get('max_abs_vega'), 4)}")
    lines.append(f"  Max |Γ|:        {_format_float(ex.get('max_abs_gamma'), 4)}")
    lines.append(f"  Max |Θ|:        {_format_float(ex.get('max_abs_theta'), 4)}")

    # --- Checks ---
    lines.append(_separator("AUTOMATED CHECKS"))
    chk_summary = chk.get("summary", {})
    lines.append(f"  {chk_summary.get('passed', 0)} / {chk_summary.get('total', 0)} passed")
    for result in chk.get("results", []):
        icon = "✅" if result["passed"] else "❌"
        lines.append(f"  {icon} {result['id']}: {result['detail']}")

    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Summarize a trading bot evaluation report")
    p.add_argument("--report", required=True, help="Path to evaluation_report.json")
    p.add_argument("--checks-only", action="store_true", help="Only print check results")
    p.add_argument("--json", action="store_true", help="Output report as JSON (passthrough)")
    args = p.parse_args(argv)

    if not os.path.exists(args.report):
        print(f"Error: report file not found: {args.report}", file=sys.stderr)
        return 1

    with open(args.report, encoding="utf-8") as f:
        report = json.load(f)

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        return 0

    if args.checks_only:
        chk = report.get("checks", {})
        chk_summary = chk.get("summary", {})
        print(f"{chk_summary.get('passed', 0)}/{chk_summary.get('total', 0)} checks passed\n")
        for result in chk.get("results", []):
            icon = "✅" if result["passed"] else "❌"
            print(f"  {icon} {result['id']}: {result['detail']}")
        # Exit non-zero if any checks failed.
        if chk_summary.get("failed", 0) > 0:
            return 1
        return 0

    print(summarize_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
