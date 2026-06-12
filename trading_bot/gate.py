"""Automated promotion gate for evaluation reports.

Takes a report dict and configurable thresholds, returns a GateResult
with pass/fail + detailed reasons. Designed for CI/CD and automated
model promotion pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class GateRule:
    metric: str
    operator: str  # "gte", "lte", "gt", "lt"
    threshold: float
    passed: bool = False
    actual: Optional[float] = None
    reason: str = ""


@dataclass
class GateResult:
    passed: bool
    rules: list[GateRule] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def failed_rules(self) -> list[GateRule]:
        return [r for r in self.rules if not r.passed]

    @property
    def passed_rules(self) -> list[GateRule]:
        return [r for r in self.rules if r.passed]


_DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "total_pnl": {"operator": "gte", "threshold": -100.0},
    "sharpe_ratio": {"operator": "gte", "threshold": 0.0},
    "sortino_ratio": {"operator": "gte", "threshold": 0.0},
    "calmar_ratio": {"operator": "gte", "threshold": 0.0},
    "max_drawdown_pct": {"operator": "gte", "threshold": -0.50},  # no worse than -50%
    "profit_factor": {"operator": "gte", "threshold": 0.5},
    "win_rate": {"operator": "gte", "threshold": 0.3},
    "expectancy": {"operator": "gte", "threshold": -5.0},
    "block_rate": {"operator": "lte", "threshold": 0.90},  # no more than 90% blocked
    "max_consecutive_losses": {"operator": "lte", "threshold": 20},
}


def _evaluate_rule(metric_name: str, actual: Optional[float], rule_def: dict) -> GateRule:
    """Evaluate a single gate rule against a metric value."""
    op = rule_def["operator"]
    threshold = float(rule_def["threshold"])

    if actual is None:
        # Null metrics are treated as "not applicable" — auto-pass, but flag it.
        return GateRule(
            metric=metric_name,
            operator=op,
            threshold=threshold,
            passed=True,
            actual=None,
            reason=f"{metric_name} is N/A (null) — auto-passed",
        )

    passed = False
    if op == "gte":
        passed = actual >= threshold
    elif op == "lte":
        passed = actual <= threshold
    elif op == "gt":
        passed = actual > threshold
    elif op == "lt":
        passed = actual < threshold

    reason = (
        f"{metric_name}: {actual:.4f} {op} {threshold:.4f} → {'PASS' if passed else 'FAIL'}"
    )

    return GateRule(
        metric=metric_name,
        operator=op,
        threshold=threshold,
        passed=passed,
        actual=actual,
        reason=reason,
    )


def should_promote(
    report: dict,
    thresholds: Optional[dict] = None,
) -> GateResult:
    """Determine whether a strategy run meets promotion criteria.

    Args:
        report: An evaluation report dict (as produced by build_evaluation_report).
        thresholds: Optional dict mapping metric names to {"operator": str, "threshold": float}.
                    If None, uses _DEFAULT_THRESHOLDS.

    Returns:
        GateResult with pass/fail and per-rule details.
    """
    if thresholds is None:
        thresholds = _DEFAULT_THRESHOLDS.copy()
    # Normalize to the expected format.
    normalized: dict[str, dict] = {}
    for k, v in thresholds.items():
        if isinstance(v, (int, float)):
            normalized[k] = {"operator": "gte", "threshold": float(v)}
        elif isinstance(v, dict):
            normalized[k] = v
        else:
            raise ValueError(f"Invalid threshold for {k}: {v!r}")

    summary = report.get("summary", {})
    checks = report.get("checks", {})
    rules: list[GateRule] = []

    # Evaluate each metric rule.
    for metric_name, rule_def in normalized.items():
        actual = summary.get(metric_name)
        rule = _evaluate_rule(metric_name, actual, rule_def)
        rules.append(rule)

    # Always require all automated checks to pass.
    check_summary = checks.get("summary", {})
    check_passed = check_summary.get("failed", 0) == 0
    check_rule = GateRule(
        metric="all_checks_pass",
        operator="eq",
        threshold=0.0,
        passed=check_passed,
        actual=float(check_summary.get("failed", 0)),
        reason=f"Checks: {check_summary.get('passed', 0)}/{check_summary.get('total', 0)} passed, "
                f"{check_summary.get('failed', 0)} failed",
    )
    rules.append(check_rule)

    all_passed = all(r.passed for r in rules)
    return GateResult(
        passed=all_passed,
        rules=rules,
        reasons=[r.reason for r in rules],
    )
