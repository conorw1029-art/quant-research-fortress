def go_no_go(
    report: Dict,
    min_dsr: float = 1.0,
    min_pf: float = 1.25,
    max_dd_dollars: float = 2000.0,
    min_trades: int = 30,
    require_both_halves: bool = True,
    dsr_waiver_threshold: float = 3.0,
    max_p_value: float = 0.05,
    instrument: Optional[str] = None,
    cost_scenario: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Go/no-go decision. v2 changes:
      - win_rate criterion removed (WR is meaningless without R:R).
      - both_halves waived when DSR >= dsr_waiver_threshold.
      - max_dd_dollars replaces max_dd_pts (confirmed: oos_max_drawdown is $).
    """
    s = report["standard"]
    d = report["dsr"]

    dsr_value = float(d.get("dsr", 0.0))
    n_trades  = int(s.get("n_trades", 0))
    pf        = float(s.get("profit_factor", 0.0))
    max_dd    = float(abs(s.get("max_drawdown_abs", s.get("max_drawdown", 0.0))))
    p_value   = float(s.get("p_value", 1.0))
    both_half = bool(s.get("both_halves_positive", False))
    mean_pnl  = float(s.get("mean_pnl", 0.0))

    dsr_waiver        = dsr_value >= dsr_waiver_threshold
    both_halves_pass  = both_half or (not require_both_halves) or dsr_waiver

    checks = {
        "n_trades":          (n_trades  >= min_trades,     n_trades,  f">= {min_trades}"),
        "dsr":               (dsr_value >= min_dsr,        dsr_value, f">= {min_dsr}"),
        "profit_factor":     (pf        >= min_pf,         pf,        f">= {min_pf}"),
        "max_drawdown_usd":  (max_dd    <= max_dd_dollars, max_dd,    f"<= ${max_dd_dollars:,.0f}"),
        "p_value":           (p_value   <= max_p_value,    p_value,   f"<= {max_p_value}"),
        "both_halves":       (both_halves_pass, both_half,
                              f"True OR DSR>={dsr_waiver_threshold}" if require_both_halves else "n/a"),
        "mean_pnl_positive": (mean_pnl  > 0,               mean_pnl,  "> 0"),
    }

    failures = [k for k, (passed, *_) in checks.items() if not passed]
    return {
        "verdict":           "PASS" if not failures else "FAIL",
        "checks":            checks,
        "failures":          failures,
        "n_failures":        len(failures),
        "dsr_waiver_active": dsr_waiver,
        "instrument":        instrument,
        "cost_scenario":     cost_scenario,
    }
