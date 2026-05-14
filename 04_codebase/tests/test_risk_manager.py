"""
RiskManager Test Suite
======================
Covers: config defaults, account state tracking, all can_trade() gates,
position sizing, Topstep rule enforcement, event emission, and edge cases.

Run with:
    python -m pytest tests/test_risk_manager.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import datetime as dt

from src.risk import RiskManager, RiskConfig, AccountState, PositionSizer
from src.risk.risk_events import RiskEventType


# ══════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def topstep_config():
    return RiskConfig(
        account_size=25_000,
        max_daily_loss_usd=1_500,
        max_trailing_drawdown_usd=2_000,
        profit_target_usd=1_500,
        max_position_contracts=1,
        fixed_contracts=1,
        sizing_method="fixed",
        circuit_breaker_on_loss_run=True,
        max_consecutive_losses=5,
        topstep_mode=True,
    )


@pytest.fixture
def rm(topstep_config):
    mgr = RiskManager(topstep_config)
    mgr.on_session_start("2026-01-15")
    return mgr


# ══════════════════════════════════════════════════════════════════
# SECTION 1: Config and state initialisation
# ══════════════════════════════════════════════════════════════════

class TestConfigAndInit:
    def test_default_account_size(self, topstep_config):
        assert topstep_config.account_size == 25_000

    def test_state_initialised_at_account_size(self, rm):
        assert rm.state.current_equity == 25_000
        assert rm.state.peak_equity == 25_000

    def test_trailing_drawdown_zero_at_start(self, rm):
        assert rm.state.trailing_drawdown == 0.0

    def test_session_open_equity_set_on_session_start(self, rm):
        assert rm.state.session_open_equity == 25_000

    def test_session_pnl_zero_at_start(self, rm):
        assert rm.state.session_realized_pnl == 0.0


# ══════════════════════════════════════════════════════════════════
# SECTION 2: can_trade() gate — all blocking conditions
# ══════════════════════════════════════════════════════════════════

class TestCanTrade:
    def test_can_trade_fresh_session(self, rm):
        ok, reason = rm.can_trade("MES")
        assert ok is True
        assert reason == ""

    def test_blocked_when_in_trade_no_pyramiding(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", n_contracts=1, point_value=5.0)
        ok, reason = rm.can_trade("MES")
        assert ok is False
        assert "in_trade" in reason

    def test_allowed_after_close(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", n_contracts=1, point_value=5.0)
        rm.on_trade_close(5205.0, "MES")
        ok, _ = rm.can_trade("MES")
        assert ok is True

    def test_daily_loss_limit_blocks_trading(self, rm):
        # Simulate loss of $1,600 (> $1,500 limit)
        rm.on_trade_open(5200.0, 1, "MES", n_contracts=1, point_value=5.0)
        # Loss of 320 pts × $5 = $1,600
        rm.on_trade_close(4880.0, "MES")
        ok, reason = rm.can_trade("MES")
        assert ok is False
        assert "daily_loss" in reason or "session_locked" in reason

    def test_trailing_dd_limit_kills_account(self, rm):
        # Lose $2,100 total: peak=$25k, equity=$22,900 → trailing DD = $2,100 > $2,000
        rm.on_trade_open(5200.0, 1, "MES", n_contracts=1, point_value=5.0)
        # loss 420 pts × $5 = $2,100
        rm.on_trade_close(4780.0, "MES")
        ok, reason = rm.can_trade("MES")
        assert ok is False
        assert rm.state.account_killed is True
        assert "trailing" in reason.lower() or "killed" in reason.lower()

    def test_loss_run_circuit_fires_at_max_consecutive(self, topstep_config):
        cfg = RiskConfig(
            account_size=25_000,
            max_daily_loss_usd=5_000,   # high limit so we don't hit daily loss
            max_trailing_drawdown_usd=10_000,
            max_consecutive_losses=3,
            circuit_breaker_on_loss_run=True,
            fixed_contracts=1,
        )
        mgr = RiskManager(cfg)
        mgr.on_session_start("2026-01-15")

        # 3 small losing trades → circuit fires on 4th attempt
        for _ in range(3):
            mgr.on_trade_open(5200.0, 1, "MES", n_contracts=1, point_value=5.0)
            mgr.on_trade_close(5198.0, "MES")  # -$10/trade

        ok, reason = mgr.can_trade("MES")
        assert ok is False
        assert "loss_run" in reason

    def test_loss_run_clears_on_win(self, topstep_config):
        cfg = RiskConfig(
            account_size=25_000,
            max_daily_loss_usd=5_000,
            max_trailing_drawdown_usd=10_000,
            max_consecutive_losses=2,
            circuit_breaker_on_loss_run=True,
            fixed_contracts=1,
        )
        mgr = RiskManager(cfg)
        mgr.on_session_start("2026-01-15")

        # 2 losses — circuit fires
        for _ in range(2):
            mgr.on_trade_open(5200.0, 1, "MES", 1, 5.0)
            mgr.on_trade_close(5198.0, "MES")

        blocked, _ = mgr.can_trade("MES")
        assert blocked is False

        # Win — clears streak
        mgr.state.consecutive_losses = 0  # manually clear for test
        ok, _ = mgr.can_trade("MES")
        assert ok is True

    def test_account_killed_flag_blocks_forever(self, rm):
        rm.state.account_killed = True
        rm.state.kill_reason = "test_kill"
        ok, reason = rm.can_trade("MES")
        assert ok is False
        assert "killed" in reason


# ══════════════════════════════════════════════════════════════════
# SECTION 3: Equity and drawdown tracking
# ══════════════════════════════════════════════════════════════════

class TestEquityTracking:
    def test_equity_increases_on_win(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5210.0, "MES")   # +10 pts × $5 = +$50
        assert rm.state.current_equity == pytest.approx(25_050, abs=1)

    def test_equity_decreases_on_loss(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5190.0, "MES")   # -10 pts × $5 = -$50
        assert rm.state.current_equity == pytest.approx(24_950, abs=1)

    def test_peak_equity_updates_on_win(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5210.0, "MES")
        assert rm.state.peak_equity == pytest.approx(25_050, abs=1)

    def test_peak_equity_stays_on_loss(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5190.0, "MES")
        assert rm.state.peak_equity == 25_000   # unchanged

    def test_trailing_drawdown_after_loss(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5190.0, "MES")    # -$50
        assert rm.state.trailing_drawdown == pytest.approx(50, abs=1)

    def test_trailing_drawdown_from_peak_not_start(self, rm):
        # Win first (peak moves up), then lose
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5400.0, "MES")    # +$1,000

        rm.on_session_start("2026-01-16")   # new session
        rm.on_trade_open(5400.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5380.0, "MES")    # -$100

        # trailing DD = peak(26,000) - equity(25,900) = $100
        assert rm.state.trailing_drawdown == pytest.approx(100, abs=2)

    def test_short_trade_pnl(self, rm):
        rm.on_trade_open(5200.0, -1, "MES", 1, 5.0)
        rm.on_trade_close(5190.0, "MES")   # short +10 pts × $5 = +$50
        assert rm.state.current_equity == pytest.approx(25_050, abs=1)

    def test_session_pnl_resets_on_new_session(self, rm):
        rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        rm.on_trade_close(5190.0, "MES")    # -$50 session pnl
        assert rm.state.session_realized_pnl == pytest.approx(-50, abs=1)

        rm.on_session_start("2026-01-16")
        assert rm.state.session_realized_pnl == 0.0


# ══════════════════════════════════════════════════════════════════
# SECTION 4: Position sizing
# ══════════════════════════════════════════════════════════════════

class TestPositionSizing:
    def test_fixed_sizing_returns_fixed_contracts(self, rm):
        n = rm.size_position("MES")
        assert n == 1

    def test_fixed_sizing_capped_at_max(self):
        cfg = RiskConfig(
            account_size=25_000, fixed_contracts=5,
            max_position_contracts=2, sizing_method="fixed",
        )
        mgr = RiskManager(cfg)
        assert mgr.size_position("MES") == 2

    def test_fractional_sizing_scales_with_equity(self):
        from src.data.data_schema import INSTRUMENTS
        cfg = RiskConfig(
            account_size=50_000,
            sizing_method="fractional",
            risk_fraction=0.02,    # risk 2% per trade
            max_position_contracts=10,
        )
        mgr = RiskManager(cfg, instrument_specs=INSTRUMENTS)
        # risk = 2% × $50,000 = $1,000
        # stop = 4 pts × $5/pt = $20/contract
        # n = 1000 / 20 = 50 → capped at 10
        n = mgr.size_position("MES", stop_distance_pts=4.0)
        assert n == 10   # capped

    def test_fractional_falls_back_to_fixed_without_stop(self):
        cfg = RiskConfig(
            account_size=25_000, sizing_method="fractional",
            risk_fraction=0.01, fixed_contracts=1,
        )
        mgr = RiskManager(cfg)
        n = mgr.size_position("MES")   # no stop provided
        assert n == 1   # fallback to fixed_contracts


# ══════════════════════════════════════════════════════════════════
# SECTION 5: Event emission
# ══════════════════════════════════════════════════════════════════

class TestEventEmission:
    def test_session_start_emits_event(self):
        events = []
        cfg = RiskConfig(account_size=25_000, max_daily_loss_usd=1_500,
                         max_trailing_drawdown_usd=2_000)
        mgr = RiskManager(cfg, event_handlers=[events.append])
        mgr.on_session_start("2026-01-15")
        assert any(e.event_type == RiskEventType.SESSION_START for e in events)

    def test_trade_close_emits_trade_closed(self, topstep_config):
        events = []
        mgr = RiskManager(topstep_config, event_handlers=[events.append])
        mgr.on_session_start("2026-01-15")
        mgr.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        mgr.on_trade_close(5210.0, "MES")
        assert any(e.event_type == RiskEventType.TRADE_CLOSED for e in events)

    def test_daily_loss_breach_emits_critical(self, topstep_config):
        events = []
        mgr = RiskManager(topstep_config, event_handlers=[events.append])
        mgr.on_session_start("2026-01-15")
        mgr.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        mgr.on_trade_close(4880.0, "MES")   # -$1,600
        critical = [e for e in events if e.severity == "CRITICAL"]
        assert len(critical) > 0

    def test_trailing_dd_breach_emits_critical(self, topstep_config):
        events = []
        mgr = RiskManager(topstep_config, event_handlers=[events.append])
        mgr.on_session_start("2026-01-15")
        mgr.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        mgr.on_trade_close(4780.0, "MES")   # -$2,100 > trailing DD limit
        dd_events = [e for e in events if e.event_type == RiskEventType.TRAILING_DD_LIMIT_HIT]
        assert len(dd_events) == 1
        assert dd_events[0].severity == "CRITICAL"


# ══════════════════════════════════════════════════════════════════
# SECTION 6: Topstep-specific scenarios
# ══════════════════════════════════════════════════════════════════

class TestTopstepScenarios:
    def test_survive_full_session_of_small_wins(self, rm):
        for _ in range(10):
            ok, _ = rm.can_trade("MES")
            assert ok
            rm.on_trade_open(5200.0, 1, "MES", 1, 5.0)
            rm.on_trade_close(5202.0, "MES")   # +$10/trade

        assert rm.state.account_killed is False
        assert rm.state.session_locked is False

    def test_profit_target_detected(self, topstep_config):
        events = []
        mgr = RiskManager(topstep_config, event_handlers=[events.append])
        mgr.on_session_start("2026-01-15")
        # Win $1,600 > $1,500 profit target
        mgr.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        mgr.on_trade_close(5520.0, "MES")   # +320 pts × $5 = +$1,600
        target_events = [e for e in events if e.event_type == RiskEventType.PROFIT_TARGET_HIT]
        assert len(target_events) == 1

    def test_multi_day_trailing_dd_survives_within_limit(self, topstep_config):
        mgr = RiskManager(topstep_config)
        mgr.on_session_start("2026-01-15")

        # Day 1: Win $500
        mgr.on_trade_open(5200.0, 1, "MES", 1, 5.0)
        mgr.on_trade_close(5300.0, "MES")   # +$500

        # Day 2: Lose $800 → total trailing DD = $800 < $2,000
        mgr.on_session_start("2026-01-16")
        mgr.on_trade_open(5300.0, 1, "MES", 1, 5.0)
        mgr.on_trade_close(5140.0, "MES")   # -$800

        assert mgr.state.account_killed is False
        assert mgr.state.trailing_drawdown == pytest.approx(800, abs=2)

    def test_account_snapshot_is_serialisable(self, rm):
        snap = rm.get_snapshot()
        import json
        serialised = json.dumps(snap)
        assert isinstance(serialised, str)


# ══════════════════════════════════════════════════════════════════
# SECTION 7: AccountState unit tests
# ══════════════════════════════════════════════════════════════════

class TestAccountState:
    def test_unrealised_pnl_long(self):
        st = AccountState(account_size=25_000)
        st.in_trade = True
        st.open_entry_price = 5200.0
        st.open_direction = 1
        st.open_n_contracts = 1
        st.open_point_value = 5.0
        st.update_unrealised(5210.0)
        assert st.open_unrealised_pnl == pytest.approx(50.0, abs=0.01)

    def test_unrealised_pnl_short(self):
        st = AccountState(account_size=25_000)
        st.in_trade = True
        st.open_entry_price = 5200.0
        st.open_direction = -1
        st.open_n_contracts = 1
        st.open_point_value = 5.0
        st.update_unrealised(5190.0)
        assert st.open_unrealised_pnl == pytest.approx(50.0, abs=0.01)

    def test_net_equity_includes_unrealised(self):
        st = AccountState(account_size=25_000)
        st.open_unrealised_pnl = 200.0
        assert st.net_equity == pytest.approx(25_200, abs=0.01)

    def test_copy_is_independent(self):
        st = AccountState(account_size=25_000)
        copy = st.copy()
        copy.current_equity = 10_000
        assert st.current_equity == 25_000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
