"""
tick_stale_bar_detector.py — Stale Bar Detection for Live Signal Systems
=========================================================================
Checks whether bar data is fresh enough to generate reliable signals.
If bars are stale (bar builder stopped, data gap, market holiday), blocks
signal generation and raises alerts.

Thresholds by timeframe (configurable):
  1m  bars: stale if newest bar > 3 minutes old
  3m  bars: stale if newest bar > 6 minutes old
  5m  bars: stale if newest bar > 10 minutes old
  15m bars: stale if newest bar > 25 minutes old
  30m bars: stale if newest bar > 45 minutes old

Usage:
  from tick_stale_bar_detector import StaleBarDetector, StalenessResult

  detector = StaleBarDetector()
  result = detector.check(bars_df, timeframe="1m")
  if result.is_stale:
      print(f"STALE: {result.age_minutes:.1f} min old — {result.reason}")
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

# Default staleness thresholds in minutes, keyed by timeframe string
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "1m":  3.0,
    "1min": 3.0,
    "3m":  6.0,
    "3min": 6.0,
    "5m":  10.0,
    "5min": 10.0,
    "15m": 25.0,
    "15min": 25.0,
    "30m": 45.0,
    "30min": 45.0,
}

# Market session hours where stale bars are NOT expected to fire false alarms:
# CME futures trade nearly 24h, so stale detection applies 24/7 except:
# - CME maintenance window: Friday 16:00 ET — Sunday 17:00 ET (approximate)
# - Daily maintenance: Mon–Thu 16:00–17:00 ET
# Outside these windows, a stale bar is a genuine gap to investigate.

CME_MAINTENANCE_WINDOWS = [
    # (weekday, hour_start_et, hour_end_et) — weekday: Mon=0, Sun=6
    # Daily: 16:00–17:00 ET Mon–Thu
    (0, 16, 17),  # Monday
    (1, 16, 17),  # Tuesday
    (2, 16, 17),  # Wednesday
    (3, 16, 17),  # Thursday
    # Weekend: Fri 16:00 – Sun 17:00 handled separately
]


@dataclass
class StalenessResult:
    symbol: str
    timeframe: str
    is_stale: bool
    newest_bar_time: Optional[pd.Timestamp]
    check_time_utc: datetime
    age_minutes: float
    threshold_minutes: float
    reason: str
    is_maintenance_window: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "is_stale": self.is_stale,
            "newest_bar_time": str(self.newest_bar_time) if self.newest_bar_time else None,
            "check_time_utc": self.check_time_utc.isoformat(),
            "age_minutes": round(self.age_minutes, 1),
            "threshold_minutes": self.threshold_minutes,
            "reason": self.reason,
            "is_maintenance_window": self.is_maintenance_window,
        }


class StaleBarDetector:
    """
    Detects stale bar data for any symbol and timeframe.
    Integrates with the manual signal engine to block signals on stale data.
    """

    def __init__(
        self,
        thresholds: Optional[Dict[str, float]] = None,
        log_dir: Optional[Path] = None,
    ):
        self.thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
        self.log_dir = log_dir or Path(__file__).parent.parent / "06_live_trading" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def check(
        self,
        bars: pd.DataFrame,
        symbol: str,
        timeframe: str = "1m",
        now_utc: Optional[datetime] = None,
    ) -> StalenessResult:
        """
        Check whether the bars DataFrame contains fresh enough data.

        Args:
            bars: DataFrame with a DatetimeIndex (UTC-aware or naive UTC)
            symbol: Contract symbol for logging
            timeframe: Bar timeframe string (e.g. "1m", "5m", "30m")
            now_utc: Override for current time (for testing)

        Returns:
            StalenessResult with is_stale=True if data is too old
        """
        now = now_utc or datetime.now(timezone.utc)
        threshold = self.thresholds.get(timeframe, self.thresholds.get("1m", 3.0))

        if bars.empty:
            return StalenessResult(
                symbol=symbol,
                timeframe=timeframe,
                is_stale=True,
                newest_bar_time=None,
                check_time_utc=now,
                age_minutes=float("inf"),
                threshold_minutes=threshold,
                reason="No bars available — DataFrame is empty",
            )

        newest_bar = bars.index[-1]
        if newest_bar.tzinfo is None:
            newest_bar = newest_bar.tz_localize("UTC")
        else:
            newest_bar = newest_bar.tz_convert("UTC")

        now_ts = pd.Timestamp(now)
        age_minutes = (now_ts - newest_bar).total_seconds() / 60

        # Check CME maintenance window
        in_maintenance = self._in_maintenance_window(now)

        if age_minutes <= threshold:
            return StalenessResult(
                symbol=symbol,
                timeframe=timeframe,
                is_stale=False,
                newest_bar_time=newest_bar,
                check_time_utc=now,
                age_minutes=age_minutes,
                threshold_minutes=threshold,
                reason=f"FRESH — {age_minutes:.1f} min old (threshold: {threshold} min)",
                is_maintenance_window=in_maintenance,
            )

        if in_maintenance:
            reason = (
                f"CME maintenance window — {age_minutes:.1f} min old but expected "
                f"(threshold {threshold} min, maintenance active)"
            )
            return StalenessResult(
                symbol=symbol,
                timeframe=timeframe,
                is_stale=False,  # Not truly stale during maintenance
                newest_bar_time=newest_bar,
                check_time_utc=now,
                age_minutes=age_minutes,
                threshold_minutes=threshold,
                reason=reason,
                is_maintenance_window=True,
            )

        return StalenessResult(
            symbol=symbol,
            timeframe=timeframe,
            is_stale=True,
            newest_bar_time=newest_bar,
            check_time_utc=now,
            age_minutes=age_minutes,
            threshold_minutes=threshold,
            reason=(
                f"STALE — newest bar is {age_minutes:.1f} min old "
                f"(threshold: {threshold} min). Bar builder may have stopped."
            ),
            is_maintenance_window=in_maintenance,
        )

    def check_file(
        self,
        bar_path: Path,
        symbol: str,
        timeframe: str = "1m",
        now_utc: Optional[datetime] = None,
    ) -> StalenessResult:
        """Convenience wrapper that loads bars from a parquet file."""
        try:
            bars = pd.read_parquet(bar_path, columns=["close"])
        except Exception as e:
            now = now_utc or datetime.now(timezone.utc)
            threshold = self.thresholds.get(timeframe, 3.0)
            return StalenessResult(
                symbol=symbol,
                timeframe=timeframe,
                is_stale=True,
                newest_bar_time=None,
                check_time_utc=now,
                age_minutes=float("inf"),
                threshold_minutes=threshold,
                reason=f"Cannot read bar file: {e}",
            )
        return self.check(bars, symbol, timeframe, now_utc)

    def check_all(
        self,
        bar_dir: Path,
        symbols: list,
        timeframe: str = "1m",
        now_utc: Optional[datetime] = None,
    ) -> Dict[str, StalenessResult]:
        """Check multiple symbols at once. Returns {symbol: StalenessResult}."""
        results = {}
        for symbol in symbols:
            bar_path = bar_dir / f"{symbol}_bars_{timeframe}.parquet"
            results[symbol] = self.check_file(bar_path, symbol, timeframe, now_utc)
        return results

    def log_result(self, result: StalenessResult) -> None:
        """Append a staleness check result to the daily log."""
        date_str = result.check_time_utc.strftime("%Y%m%d")
        log_path = self.log_dir / f"stale_bar_checks_{date_str}.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

    @staticmethod
    def _in_maintenance_window(now: datetime) -> bool:
        """True if now is inside a known CME maintenance window."""
        import pytz
        ET = pytz.timezone("America/New_York")
        et_now = datetime.fromtimestamp(now.timestamp(), tz=ET)
        weekday = et_now.weekday()  # Mon=0, Sun=6
        hour = et_now.hour

        # Weekend: Friday 16:00 ET through Sunday 17:00 ET
        if weekday == 4 and hour >= 16:  # Friday after 16:00
            return True
        if weekday == 5:  # Saturday entirely
            return True
        if weekday == 6 and hour < 17:  # Sunday before 17:00
            return True

        # Daily Mon–Thu: 16:00–17:00 ET
        for (wd, h_start, h_end) in CME_MAINTENANCE_WINDOWS:
            if weekday == wd and h_start <= hour < h_end:
                return True

        return False


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    detector = StaleBarDetector()

    bar_dir = Path(__file__).parent.parent / "01_data" / "tick_bars"
    symbols = ["GC", "SI"]

    print("Stale Bar Detector — checking current bar files")
    print("=" * 60)

    for symbol in symbols:
        bar_path = bar_dir / f"{symbol}_bars_1m.parquet"
        result = detector.check_file(bar_path, symbol, "1m")
        status = "STALE" if result.is_stale else "FRESH"
        print(f"[{symbol}] {status} — {result.reason}")

    print("\nThresholds by timeframe:")
    for tf, mins in sorted(detector.thresholds.items()):
        print(f"  {tf}: {mins} minutes")
