"""
VIX Regime Overlay
==================
Wraps existing surviving strategies with a volatility regime filter.

Hypothesis: Mean-reversion strategies (Bollinger RSI) work best in HIGH-vol
regimes (overshoots more frequent). Trend strategies (Donchian) work best
in LOW-vol regimes (cleaner trends, less whipsaw).

Approach:
  - Compute 20-day realized vol on ES (proxy for VIX).
  - Take base strategy signals.
  - In HIGH-vol regime variant: only take signals when realized_vol >= 75th percentile.
  - In LOW-vol regime variant: only take signals when realized_vol <= 25th percentile.

Why proxy VIX with ES realized vol:
  - VIX correlation with 20-day realized ES vol is ~0.85 historically.
  - Avoids needing to download/sync VIX futures data.
  - Same regime classification effect.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Type
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


# Cache for ES daily realized vol so we don't recompute per strategy
_es_vol_cache: Optional[pd.Series] = None


def _load_es_realized_vol() -> pd.Series:
    """Load ES 1min, compute 20-day realized vol, cache result."""
    global _es_vol_cache
    if _es_vol_cache is not None:
        return _es_vol_cache

    from pathlib import Path
    es_path = Path('..') / '01_data' / 'raw' / 'ES_1min.csv'
    if not es_path.exists():
        raise FileNotFoundError(f"ES_1min.csv not found at {es_path}")

    df = pd.read_csv(es_path, usecols=[0, 4])  # ts_event, close
    df.columns = [c.lower() for c in df.columns]
    ts = next(c for c in df.columns if 'ts_event' in c or 'timestamp' in c)
    close_col = next(c for c in df.columns if c == 'close')
    df[ts] = pd.to_datetime(df[ts], utc=True, errors='coerce')
    df = df.dropna().set_index(ts)
    df.index = df.index.tz_convert('America/New_York').tz_localize(None)

    # Daily close
    daily_close = df[close_col].resample('1D').last().dropna()
    daily_returns = daily_close.pct_change()
    # 20-day rolling realized vol, annualized
    realized_vol = daily_returns.rolling(20, min_periods=15).std() * np.sqrt(252)
    _es_vol_cache = realized_vol
    return realized_vol


def _get_regime_thresholds(realized_vol: pd.Series) -> tuple[float, float]:
    """Returns (low_threshold_25th, high_threshold_75th) percentiles."""
    return (
        float(realized_vol.quantile(0.25)),
        float(realized_vol.quantile(0.75)),
    )


class VixRegimeOverlay(BaseStrategy):
    """
    Generic regime overlay. Subclassed for each base strategy.

    Parameters
    ----------
    regime : "high" or "low"
        Which volatility regime to filter for.
    """
    name = "VIX_Regime_Overlay"
    category = "regime_filter"
    timeframe = "5min"
    version = "1.0"
    BASE_STRATEGY_CLS: Optional[Type[BaseStrategy]] = None  # set by subclass

    param_grid = {"regime": ["high", "low"]}

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"regime": "high"}
        if self.BASE_STRATEGY_CLS is None:
            raise ValueError(f"{self.__class__.__name__} must set BASE_STRATEGY_CLS")
        self._base = self.BASE_STRATEGY_CLS()
        # Inherit max_trades_per_day from base
        self.max_trades_per_day = getattr(self._base, "max_trades_per_day", 1)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # Get base strategy signals
        base_sig = self._base.generate_signals(data)

        # Load realized vol regime classification
        realized_vol = _load_es_realized_vol()
        low_thresh, high_thresh = _get_regime_thresholds(realized_vol)
        regime = self.params["regime"]

        # For each bar in data, find the corresponding daily realized vol
        # and decide whether the regime allows trading.
        bar_dates = pd.Series(data.index.normalize(), index=data.index)
        rv_aligned = bar_dates.map(lambda d: realized_vol.get(d, np.nan))

        if regime == "high":
            allowed = rv_aligned >= high_thresh
        else:  # low
            allowed = rv_aligned <= low_thresh

        # Suppress signals where regime doesn't match
        filtered = base_sig.where(allowed, 0).astype(int)
        return filtered

    def signals_to_trades(self, data, signals, max_bars_per_trade=None):
        # Delegate to base strategy's exit logic
        if max_bars_per_trade is None:
            return self._base.signals_to_trades(data, signals)
        return self._base.signals_to_trades(data, signals, max_bars_per_trade)

    def trades_to_dataframe(self, trades):
        return self._base.trades_to_dataframe(trades)


# ─────────────────────────────────────────────────────────────────────
# CONCRETE OVERLAYS — one per existing survivor
# ─────────────────────────────────────────────────────────────────────

# Lazy imports to avoid circular dependencies
def _make_overlay(base_cls_name: str, base_module: str):
    """Factory to create an overlay class with the right base strategy."""
    import importlib
    mod = importlib.import_module(base_module)
    base_cls = getattr(mod, base_cls_name)
    
    class _Overlay(VixRegimeOverlay):
        BASE_STRATEGY_CLS = base_cls
        name = f"VIX_{base_cls.__name__}"
    
    _Overlay.__name__ = f"VIX_{base_cls_name}"
    return _Overlay


# Generated overlay classes
VIXBollingerRSI = _make_overlay("BollingerRSIStrategy", "src.strategies.bollinger_rsi")
VIXDonchianBreakout = _make_overlay("DonchianBreakoutStrategy", "src.strategies.donchian_breakout")
VIXFomcDrift = _make_overlay("FOMCDriftStrategy", "src.strategies.fomc_drift")


# ── Registry aliases (registry uses VIXOverlayStrategy for all VIX entries) ──
# The registry was generated expecting a single generic class name.
# Each entry passes the base strategy via params instead.
# We implement VIXOverlayStrategy as a generic dispatcher.

class VIXOverlayStrategy(VixRegimeOverlay):
    """
    Generic VIX overlay dispatcher.
    Reads 'base_strategy_key' from params to select the underlying strategy.
    Supported keys: bollinger_rsi_gc, bollinger_rsi_fxe, donchian_cl, fomc_es, fomc_zn
    """
    name = "VIX_Overlay"
    BASE_STRATEGY_CLS = None  # determined at runtime from params

    _KEY_MAP = {
        "bollinger_rsi": ("src.strategies.bollinger_rsi", "BollingerRSIStrategy"),
        "donchian": ("src.strategies.donchian_breakout", "DonchianBreakoutStrategy"),
        "fomc": ("src.strategies.fomc_drift", "FOMCDriftStrategy"),
    }

    def __init__(self, params=None):
        # Don't call super().__init__() yet — need to resolve base class first
        self.params = params or {"regime": "high"}
        self._base = self._resolve_base()
        self.max_trades_per_day = getattr(self._base, "max_trades_per_day", 1)

    def _resolve_base(self):
        import importlib
        # Infer which base strategy to use from the instrument context
        # Default to BollingerRSI (most common VIX overlay target)
        base_key = self.params.get("base_strategy", "bollinger_rsi")
        for k, (mod_path, cls_name) in self._KEY_MAP.items():
            if k in base_key:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                return cls()
        # Default fallback
        from src.strategies.bollinger_rsi import BollingerRSIStrategy
        return BollingerRSIStrategy()

    def generate_signals(self, data):
        base_sig = self._base.generate_signals(data)
        try:
            realized_vol = _load_es_realized_vol()
            low_thresh, high_thresh = _get_regime_thresholds(realized_vol)
            regime = self.params.get("regime", "high")
            import pandas as pd
            bar_dates = pd.Series(data.index.normalize(), index=data.index)
            rv_aligned = bar_dates.map(lambda d: realized_vol.get(d, float("nan")))
            if regime == "high":
                allowed = rv_aligned >= high_thresh
            else:
                allowed = rv_aligned <= low_thresh
            return base_sig.where(allowed, 0).astype(int)
        except Exception:
            # If ES vol data unavailable, fall back to no filter
            return base_sig

    def signals_to_trades(self, data, signals, max_bars_per_trade=None):
        if max_bars_per_trade is None:
            return self._base.signals_to_trades(data, signals)
        return self._base.signals_to_trades(data, signals, max_bars_per_trade)

    def trades_to_dataframe(self, trades):
        return self._base.trades_to_dataframe(trades)
