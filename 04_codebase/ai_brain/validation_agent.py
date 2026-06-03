"""
validation_agent.py — Runs and interprets tick evidence upgrades
================================================================
ValidationAgent executes the tick_evidence_upgrade.py subprocess and
analyses the resulting evidence JSON to determine deployment eligibility.

Deployment gates (ALL must pass):
  - wf_sharpe > 1.5
  - bootstrap_p < 0.05
  - slippage_1tick_sharpe > 0
  - n_trades >= 200

ValidationAgent does NOT deploy strategies — it only validates them and
produces reports. Deployment decisions go through DeploymentGatekeeper.

Usage:
    from ai_brain.validation_agent import ValidationAgent

    agent = ValidationAgent(decision_log=log)
    result = agent.run_evidence_upgrade(survivors_path, bars_path, l2_bars_path,
                                        tick_size=0.10, output_dir=out_dir)
    eligible = agent.check_deployment_eligibility(evidence_file)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)

# All gates must be met for a strategy to be eligible for deployment consideration
DEPLOYMENT_GATES = {
    "wf_sharpe":       ("gt", 1.5),
    "bootstrap_p":     ("lt", 0.05),
    "slippage_1tick":  ("gt", 0.0),   # slippage_1tick_sharpe must be positive
    "n_trades":        ("gte", 200),
}

# Keys that may appear in evidence files under different names
_KEY_ALIASES = {
    "wf_sharpe":      ["wf_sharpe", "sharpe", "walk_forward_sharpe"],
    "bootstrap_p":    ["bootstrap_p", "p_value", "bootstrap_pvalue"],
    "slippage_1tick": ["slippage_1tick_sharpe", "slippage_1tick", "slip_1tick_sharpe"],
    "n_trades":       ["n_trades", "total_trades", "trade_count"],
}


class ValidationAgent:
    """
    Runs evidence upgrades and checks strategy deployment eligibility.

    Args:
        decision_log:          DecisionLog instance. Created with defaults if None.
        evidence_upgrade_script: Path to tick_evidence_upgrade.py. If None,
                                  searches in the same package directory.
    """

    def __init__(
        self,
        decision_log: Optional[DecisionLog] = None,
        evidence_upgrade_script: Optional[Path] = None,
    ):
        self.dlog = decision_log or DecisionLog()
        if evidence_upgrade_script is None:
            # Try to find tick_evidence_upgrade.py relative to this file
            pkg_dir = Path(__file__).parent
            candidates = [
                pkg_dir.parent / "tick_evidence_upgrade.py",
                pkg_dir.parent / "scripts" / "tick_evidence_upgrade.py",
                pkg_dir / "tick_evidence_upgrade.py",
            ]
            for c in candidates:
                if c.exists():
                    self.upgrade_script = c
                    break
            else:
                self.upgrade_script = pkg_dir.parent / "tick_evidence_upgrade.py"
        else:
            self.upgrade_script = evidence_upgrade_script

    # ── Core methods ───────────────────────────────────────────────────────────

    def run_evidence_upgrade(
        self,
        survivors_path: Path,
        bars_path: Path,
        l2_bars_path: Path,
        tick_size: float,
        output_dir: Path,
    ) -> dict:
        """
        Run tick_evidence_upgrade.py as a subprocess and parse results.

        The script is called with -X utf8 to force UTF-8 encoding on Windows.

        Args:
            survivors_path: Path to the survivors JSON file.
            bars_path:      Path to the OHLCV bar parquet directory.
            l2_bars_path:   Path to L2/tick bar parquet directory.
            tick_size:      Instrument tick size (e.g. 0.10 for GC).
            output_dir:     Directory where output evidence JSON should be written.

        Returns:
            {
                "exit_code":   int,
                "n_passed":    int,
                "n_failed":    int,
                "output_file": str,   # path to evidence JSON or "" if not found
                "stdout":      str,
                "stderr":      str,
            }
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-X", "utf8",
            str(self.upgrade_script),
            "--survivors", str(survivors_path),
            "--bars", str(bars_path),
            "--l2-bars", str(l2_bars_path),
            "--tick-size", str(tick_size),
            "--output-dir", str(output_dir),
        ]

        self.dlog.log(
            agent="ValidationAgent",
            observation=f"Starting evidence upgrade: {self.upgrade_script.name}",
            recommendation="Monitor subprocess. Check output_dir for results.",
            action_taken="evidence_upgrade_started",
            human_approval_required=False,
            risk_level="LOW",
            metadata={
                "survivors_path": str(survivors_path),
                "bars_path": str(bars_path),
                "l2_bars_path": str(l2_bars_path),
                "tick_size": tick_size,
                "output_dir": str(output_dir),
                "cmd": cmd,
            },
        )

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,  # 1 hour max
            )
        except subprocess.TimeoutExpired:
            logger.error("[ValidationAgent] Evidence upgrade timed out after 1 hour.")
            self.dlog.log(
                agent="ValidationAgent",
                observation="Evidence upgrade subprocess timed out after 3600s.",
                recommendation="Check for performance issues or infinite loops in upgrade script.",
                action_taken="upgrade_timeout",
                human_approval_required=True,
                risk_level="HIGH",
            )
            return {
                "exit_code": -1,
                "n_passed": 0,
                "n_failed": 0,
                "output_file": "",
                "stdout": "",
                "stderr": "TIMEOUT",
            }
        except FileNotFoundError as e:
            logger.error("[ValidationAgent] Script not found: %s", e)
            self.dlog.log(
                agent="ValidationAgent",
                observation=f"Evidence upgrade script not found: {self.upgrade_script}",
                recommendation="Verify tick_evidence_upgrade.py exists at the expected path.",
                action_taken="upgrade_script_missing",
                human_approval_required=False,
                risk_level="HIGH",
            )
            return {
                "exit_code": -2,
                "n_passed": 0,
                "n_failed": 0,
                "output_file": "",
                "stdout": "",
                "stderr": str(e),
            }

        # Parse stdout for pass/fail counts
        n_passed, n_failed = self._parse_pass_fail(proc.stdout)

        # Find output evidence file
        output_file = self._find_output_file(output_dir)

        result = {
            "exit_code": proc.returncode,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "output_file": output_file,
            "stdout": proc.stdout[-5000:],   # keep last 5k chars
            "stderr": proc.stderr[-2000:],
        }

        success = proc.returncode == 0
        self.dlog.log(
            agent="ValidationAgent",
            observation=(
                f"Evidence upgrade completed: exit={proc.returncode}, "
                f"passed={n_passed}, failed={n_failed}, output={output_file}"
            ),
            recommendation=(
                "Review evidence file and run check_deployment_eligibility."
                if success
                else "Investigate stderr for errors. Do not deploy until resolved."
            ),
            action_taken="evidence_upgrade_completed",
            human_approval_required=not success,
            risk_level="MEDIUM" if success else "HIGH",
            evidence_file=output_file or None,
            metadata={"exit_code": proc.returncode, "n_passed": n_passed, "n_failed": n_failed},
        )

        return result

    def check_deployment_eligibility(self, evidence_file: Path) -> List[dict]:
        """
        Read an evidence JSON file and return strategies that pass ALL gates.

        Gates:
          - wf_sharpe > 1.5
          - bootstrap_p < 0.05
          - slippage_1tick_sharpe > 0
          - n_trades >= 200

        Args:
            evidence_file: Path to the evidence JSON produced by evidence upgrade.

        Returns:
            List of strategy dicts for strategies that pass all gates.
            Each dict includes strategy_key and all relevant metrics.
        """
        if not evidence_file.exists():
            logger.error("[ValidationAgent] Evidence file not found: %s", evidence_file)
            return []

        try:
            data = json.loads(evidence_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[ValidationAgent] Cannot read evidence file: %s", e)
            return []

        # Evidence file may be a list of strategies or a dict
        if isinstance(data, list):
            strategies = data
        elif isinstance(data, dict):
            # Could be {strategy_key: {...}} or {"strategies": [...]}
            if "strategies" in data:
                strategies = data["strategies"]
            else:
                strategies = [
                    dict(entry, strategy_key=key)
                    for key, entry in data.items()
                    if isinstance(entry, dict)
                ]
        else:
            logger.error("[ValidationAgent] Unexpected evidence format: %s", type(data))
            return []

        eligible = []
        for strat in strategies:
            metrics = self._extract_metrics(strat)
            if self._passes_all_gates(metrics):
                eligible.append({**strat, **metrics, "_gate_status": "PASS"})

        self.dlog.log(
            agent="ValidationAgent",
            observation=(
                f"Eligibility check on {evidence_file.name}: "
                f"{len(eligible)}/{len(strategies)} strategies passed all gates."
            ),
            recommendation=(
                f"Eligible strategies: {[e.get('strategy_key') for e in eligible]}"
                if eligible
                else "No strategies passed all deployment gates."
            ),
            action_taken="eligibility_checked",
            human_approval_required=False,
            risk_level="LOW",
            evidence_file=str(evidence_file),
            metadata={"n_total": len(strategies), "n_eligible": len(eligible)},
        )

        return eligible

    def produce_validation_report(
        self,
        evidence_file: Path,
        output_path: Path,
    ) -> None:
        """
        Write a JSON summary with pass/fail status and reason for each strategy.

        Args:
            evidence_file: Path to the evidence JSON.
            output_path:   Where to write the validation report JSON.
        """
        if not evidence_file.exists():
            logger.error("[ValidationAgent] Evidence file not found: %s", evidence_file)
            return

        try:
            data = json.loads(evidence_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[ValidationAgent] Cannot read evidence: %s", e)
            return

        if isinstance(data, list):
            strategies = data
        elif isinstance(data, dict):
            if "strategies" in data:
                strategies = data["strategies"]
            else:
                strategies = [
                    dict(entry, strategy_key=key)
                    for key, entry in data.items()
                    if isinstance(entry, dict)
                ]
        else:
            strategies = []

        report_entries = []
        for strat in strategies:
            metrics = self._extract_metrics(strat)
            gate_results, failed_gates = self._check_gates_detail(metrics)
            report_entries.append({
                "strategy_key": strat.get("strategy_key", "UNKNOWN"),
                "overall": "PASS" if not failed_gates else "FAIL",
                "failed_gates": failed_gates,
                "gate_results": gate_results,
                "metrics": metrics,
            })

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_file": str(evidence_file),
            "total_strategies": len(report_entries),
            "n_passed": sum(1 for e in report_entries if e["overall"] == "PASS"),
            "n_failed": sum(1 for e in report_entries if e["overall"] == "FAIL"),
            "strategies": report_entries,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            output_path.write_text(
                json.dumps(report, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("[ValidationAgent] Cannot write validation report: %s", e)
            return

        self.dlog.log(
            agent="ValidationAgent",
            observation=(
                f"Validation report written to {output_path}. "
                f"Pass={report['n_passed']}, Fail={report['n_failed']}."
            ),
            recommendation="Human should review PASS strategies with DeploymentGatekeeper.",
            action_taken="validation_report_written",
            human_approval_required=False,
            risk_level="LOW",
            evidence_file=str(output_path),
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _extract_metrics(self, strat: dict) -> dict:
        """Extract gate metric values from a strategy dict, handling key aliases."""
        metrics = {}
        for gate_key, aliases in _KEY_ALIASES.items():
            for alias in aliases:
                if alias in strat:
                    try:
                        val = float(strat[alias]) if gate_key != "n_trades" else int(strat[alias])
                    except (ValueError, TypeError):
                        val = None
                    metrics[gate_key] = val
                    break
            else:
                metrics[gate_key] = None
        return metrics

    def _passes_all_gates(self, metrics: dict) -> bool:
        """Return True if all gate conditions are met."""
        _, failed = self._check_gates_detail(metrics)
        return len(failed) == 0

    def _check_gates_detail(self, metrics: dict) -> tuple:
        """
        Check each gate and return (gate_results dict, list_of_failed_gate_names).
        """
        gate_results = {}
        failed = []

        for gate_key, (op, threshold) in DEPLOYMENT_GATES.items():
            val = metrics.get(gate_key)
            if val is None:
                gate_results[gate_key] = {"result": "MISSING", "value": None, "threshold": threshold}
                failed.append(f"{gate_key}=MISSING")
                continue

            try:
                fval = float(val)
                if op == "gt":
                    passed = fval > threshold
                elif op == "lt":
                    passed = fval < threshold
                elif op == "gte":
                    passed = fval >= threshold
                else:
                    passed = False

                gate_results[gate_key] = {
                    "result": "PASS" if passed else "FAIL",
                    "value": val,
                    "threshold": threshold,
                    "operator": op,
                }
                if not passed:
                    failed.append(f"{gate_key}={val} (need {op} {threshold})")
            except (ValueError, TypeError):
                gate_results[gate_key] = {"result": "INVALID", "value": val, "threshold": threshold}
                failed.append(f"{gate_key}=INVALID")

        return gate_results, failed

    def _parse_pass_fail(self, stdout: str) -> tuple:
        """
        Attempt to parse n_passed and n_failed from subprocess stdout.

        Looks for patterns like "N/M passed" or "passed: N" in output.
        Returns (0, 0) if nothing parsable is found.
        """
        import re

        n_passed, n_failed = 0, 0

        # Pattern: "5/5 passed" or "5 passed" or "passed: 5"
        match = re.search(r"(\d+)\s*/\s*(\d+)\s+pass", stdout, re.IGNORECASE)
        if match:
            n_passed = int(match.group(1))
            total = int(match.group(2))
            n_failed = total - n_passed
            return n_passed, n_failed

        match = re.search(r"(\d+)\s+pass", stdout, re.IGNORECASE)
        if match:
            n_passed = int(match.group(1))

        match = re.search(r"(\d+)\s+fail", stdout, re.IGNORECASE)
        if match:
            n_failed = int(match.group(1))

        return n_passed, n_failed

    def _find_output_file(self, output_dir: Path) -> str:
        """Find the most recently modified evidence JSON in output_dir."""
        if not output_dir.exists():
            return ""
        candidates = sorted(
            output_dir.glob("*evidence*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
        # Fallback: any JSON
        candidates = sorted(
            output_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else ""
