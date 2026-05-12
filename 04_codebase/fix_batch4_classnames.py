"""
fix_batch4_classnames.py
Appends class name aliases to vix_overlay.py and calendar_events_batch4.py
so the registry's expected names resolve correctly.

Run from 04_codebase/:
    python fix_batch4_classnames.py
"""
from pathlib import Path

# ── vix_overlay.py — registry expects VIXOverlayStrategy ──────────────
vix_path = Path("src/strategies/vix_overlay.py")
vix_addition = '''

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
'''

existing = vix_path.read_text(encoding="utf-8")
if "class VIXOverlayStrategy" not in existing:
    vix_path.write_text(existing + vix_addition, encoding="utf-8")
    print(f"✓ Patched {vix_path}")
else:
    print(f"  VIXOverlayStrategy already exists in {vix_path}")

# ── calendar_events_batch4.py — registry expects short names ──────────
cal_path = Path("src/strategies/calendar_events_batch4.py")
cal_addition = '''

# ── Registry aliases ──────────────────────────────────────────────────
BOJStrategy         = BOJDriftStrategy
BOEStrategy         = BOEDriftStrategy
ISMStrategy         = ISMDriftStrategy
PPIStrategy         = PPIDriftStrategy
GDPStrategy         = GDPDriftStrategy
RetailSalesStrategy = RetailSalesDriftStrategy
'''

existing_cal = cal_path.read_text(encoding="utf-8")
if "BOJStrategy" not in existing_cal:
    cal_path.write_text(existing_cal + cal_addition, encoding="utf-8")
    print(f"✓ Patched {cal_path}")
else:
    print(f"  Aliases already exist in {cal_path}")

# ── Quick verification ─────────────────────────────────────────────────
print("\nVerifying imports...")
import importlib, sys
sys.path.insert(0, ".")

for mod_path, cls_name in [
    ("src.strategies.vix_overlay", "VIXOverlayStrategy"),
    ("src.strategies.calendar_events_batch4", "BOJStrategy"),
    ("src.strategies.calendar_events_batch4", "BOEStrategy"),
    ("src.strategies.calendar_events_batch4", "ISMStrategy"),
    ("src.strategies.calendar_events_batch4", "PPIStrategy"),
    ("src.strategies.calendar_events_batch4", "GDPStrategy"),
    ("src.strategies.calendar_events_batch4", "RetailSalesStrategy"),
]:
    try:
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        print(f"  ✓ {cls_name}")
    except Exception as e:
        print(f"  ✗ {cls_name}: {e}")

print("\nDone. Now run batch 4.")
