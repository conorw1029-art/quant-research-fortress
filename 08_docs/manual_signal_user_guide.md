# Manual Signal System — User Guide

This guide covers everything needed to run the Manual Signal System during a live trading session. The system generates trade alerts from L2 bar data. No orders are placed automatically — you decide whether to enter each trade.

---

## Part 1: Pre-Flight Checklist

Complete this checklist before each session.

### 1.1 Environment Variables

Open PowerShell and set required variables:

```powershell
# Telegram (optional but recommended)
$env:TELEGRAM_BOT_TOKEN = "your_bot_token_here"
$env:TELEGRAM_CHAT_ID   = "your_chat_id_here"
```

To make these permanent (survives restarts), add them to your PowerShell profile:

```powershell
notepad $PROFILE
# Add the two $env: lines above and save
```

Test Telegram connectivity:

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -c "
import requests, os
r = requests.post(
    f'https://api.telegram.org/bot{os.environ[\"TELEGRAM_BOT_TOKEN\"]}/sendMessage',
    json={'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': 'Signal system test OK'}
)
print(r.status_code, r.text[:100])
"
```

### 1.2 Data Freshness Check

Confirm bar files were updated recently:

```powershell
Get-Item C:\Users\conor\Desktop\quant-research\01_data\tick_bars\GC_bars_l2_1m.parquet |
    Select-Object Name, LastWriteTime

Get-Item C:\Users\conor\Desktop\quant-research\01_data\tick_bars\SI_bars_l2_1m.parquet |
    Select-Object Name, LastWriteTime
```

Both files should show a LastWriteTime within the last 5 minutes during market hours. If they are older than 10 minutes during trading hours, the bar builder is not running — do not start the signal engine until data is live.

### 1.3 News Calendar Check

Before each session, check if any high-impact events are scheduled today:

- **NFP:** First Friday of each month, 8:30 AM ET (13:30 UTC). Do not trade 30 min before/after.
- **FOMC:** 8 times/year, 2:00 PM ET (19:00 UTC). 2026 dates: Jan 29, Mar 19, May 7, Jun 18, Jul 30, Sep 17, Nov 5, Dec 16.
- **CPI:** Approximately the 2nd Tuesday-Thursday of each month, 8:30 AM ET (13:30 UTC). Check the BLS schedule for exact dates.

The signal engine automatically blocks signals during these windows. However, you should be aware of upcoming events so you are not surprised by blocked signals.

### 1.4 Quick Dry-Run Test

Always test before going live:

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
    --dry-run --once --symbols GC SI
```

Expected output:
- Import errors (if any) printed to console — investigate if strategies fail to import
- "Checking CVD_Microprice on SI [1m]" — confirms strategy registration
- "Pass complete. X signal events processed." — confirms engine ran

---

## Part 2: Starting the System

### 2.1 Standard Watch Mode (Recommended)

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
    --watch --symbols SI GC --telegram
```

The engine will:
1. Load bars every 60 seconds
2. Run all deployed strategies
3. Print signals to console (box format)
4. Send fired signals to Telegram
5. Log every signal (fired + blocked) to `06_live_trading/logs/signals_YYYYMMDD.jsonl`

### 2.2 Custom Interval

Poll every 30 seconds instead of 60:

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
    --watch --interval 30 --symbols SI GC
```

### 2.3 Specific Strategies Only

Run only CVD_VWAP on GC:

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
    --watch --symbols GC --strategy-allowlist CVD_VWAP
```

### 2.4 One-Time Pass (Manual Trigger)

Run a single scan and exit (useful for scripting or spot checks):

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
    --once --symbols SI GC
```

### 2.5 Running in Background (PowerShell)

To run in the background and keep the terminal free:

```powershell
Start-Job -ScriptBlock {
    C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
        C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
        --watch --symbols SI GC --telegram
}
```

Check background job output:
```powershell
Get-Job | Receive-Job
```

Stop the background job:
```powershell
Get-Job | Stop-Job
Get-Job | Remove-Job
```

---

## Part 3: Reading a Signal Alert

When a signal fires, you will see a box like this:

```
╔══════════════════════════════════════════════════════════════╗
║ SIGNAL: CVD_Microprice | SI | LONG                           ║
║ Entry: 32.1500–32.2000  Stop: 31.9500  Target: 32.5500       ║
║ Risk: $125  R/R: 2.0  Confidence: HIGH                       ║
║ Context: cvd_delta=140 | mp=32.175 | session_vwap=32.100     ║
║ Regime: RANGING | Bar: 2026-06-03 14:30 UTC                  ║
║ Invalidation: Cancel if price < 32.050 before entry          ║
╚══════════════════════════════════════════════════════════════╝
```

### Field Explanations

| Field | What It Means |
|-------|--------------|
| **Strategy** | Which strategy fired. "CVD_Microprice" = CVD + microprice dual confirmation |
| **Symbol** | Contract to trade. "SI" = Silver futures, "GC" = Gold futures |
| **Side** | Direction. LONG = buy, SHORT = sell |
| **Entry zone** | Price range to enter. Enter anywhere in the zone (e.g., 32.15 to 32.20). Do not chase above the zone. |
| **Stop** | Stop-loss price. Place your stop here. If the trade goes against you to this level, exit. |
| **Target** | Profit target. This is your 1st target (full R/R). |
| **Risk $** | Dollar risk per contract at 1 contract, assuming fill at entry_ref. Adjust for your actual position size. |
| **R/R** | Reward-to-risk ratio. 2.0 means target is 2× the stop distance. |
| **Confidence** | HIGH = quiet market, cleaner signal. MEDIUM = normal. LOW = volatile conditions. |
| **Context** | Key L2 values at signal bar. Used to understand why the signal fired. |
| **Regime** | Market character at signal time. TRENDING = momentum likely to continue, RANGING = mean-reversion favored, VOLATILE = wider spreads, higher risk. |
| **Bar** | Exact bar that triggered the signal (UTC timestamp). |
| **Invalidation** | Price level that cancels the signal before you enter. If price reaches this before you fill, do not take the trade. |

### Signal Confidence Levels

| Confidence | Meaning | Suggested Action |
|------------|---------|-----------------|
| HIGH | ATR < 80% of 20-bar mean. Quiet, focused market. Signal more reliable. | Consider normal size |
| MEDIUM | ATR within normal range. Standard signal quality. | Standard size |
| LOW | ATR > 140% of 20-bar mean. Volatile conditions. Wider spreads, gaps. | Reduce size or skip |

---

## Part 4: Deciding Whether to Take the Trade

The signal is a starting point, not a mandate. Run this mental checklist before entering:

```
PRE-ENTRY CHECKLIST
===================
[ ] Is the signal NOT blocked? (Telegram/console message, not "[BLOCKED]")
[ ] Is the current price still inside the entry zone?
[ ] Has the invalidation condition NOT been triggered?
[ ] Confidence is MEDIUM or HIGH (or LOW is acceptable at smaller size)
[ ] No major news in the next 30 minutes (check NFP/FOMC/CPI calendar)
[ ] I understand why this signal fired (check Context values)
[ ] My daily loss limit has not been reached ($1,000 per account)
[ ] I have reviewed the stop and target — they make sense on the chart
```

If all boxes are checked: take the trade at your broker platform (Tradovate, NinjaTrader, etc.).

If any box fails: pass. There will be another signal.

### Context Value Guide

| Context Key | What It Tells You |
|-------------|------------------|
| cvd_delta | Cumulative Volume Delta for the bar. Positive = buyers dominated, negative = sellers dominated |
| cvd | Rolling CVD value used for percentile comparison |
| microprice_last | Microprice at bar close. If above close price → order book tilted bullish |
| session_vwap | Session VWAP. CVD_VWAP strategy fires when price is near this level |
| buy_sweeps | Number of buy-side order book sweeps in the bar (Sweep_Continuation strategy) |
| sell_sweeps | Number of sell-side sweeps |
| absorption_score | Positive = buyers absorbing sellers, negative = sellers absorbing buyers |
| ofi_5 | 5-level Order Flow Imbalance. Positive = more aggressive buying than selling |

---

## Part 5: Logging Your Actual Fill

The signal system does not connect to your broker, so you must record actual fills manually. This is important for comparing real vs. hypothetical performance.

### Method 1: Append to Daily JSONL (Recommended)

Open the signal log and add a fill record:

```powershell
$logPath = "C:\Users\conor\Desktop\quant-research\06_live_trading\logs\signals_$(Get-Date -Format 'yyyyMMdd').jsonl"

$fill = @{
    timestamp        = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    record_type      = "actual_fill"
    strategy_name    = "CVD_Microprice"
    symbol           = "SI"
    side             = "LONG"
    actual_fill_price = 32.175
    actual_stop      = 31.950
    actual_target    = 32.550
    actual_size      = 1
    notes            = "Filled at ask, clean entry"
} | ConvertTo-Json -Compress

Add-Content -Path $logPath -Value $fill
```

### Method 2: Manual Trade Journal

Keep a separate text file or spreadsheet with:
- Date/time of entry
- Strategy that generated the signal
- Symbol, side, fill price, stop, target
- Exit price and outcome (WIN/LOSS/BE)
- Notes on execution quality

---

## Part 6: Ending the Session

### 6.1 Stop the Engine

If running in the foreground: press `Ctrl+C` in the PowerShell terminal.

If running as a background job:
```powershell
Get-Job | Stop-Job
Get-Job | Remove-Job
```

### 6.2 Verify Log File

```powershell
Get-Item "C:\Users\conor\Desktop\quant-research\06_live_trading\logs\signals_$(Get-Date -Format 'yyyyMMdd').jsonl"
```

Confirm the file exists and has a non-zero size.

### 6.3 Optional: Quick Signal Count

```powershell
$logPath = "C:\Users\conor\Desktop\quant-research\06_live_trading\logs\signals_$(Get-Date -Format 'yyyyMMdd').jsonl"
$records = Get-Content $logPath | ForEach-Object { $_ | ConvertFrom-Json }
$fired   = ($records | Where-Object { -not $_.is_blocked -and $_.side -ne 'N/A' }).Count
$blocked = ($records | Where-Object { $_.is_blocked }).Count
Write-Host "Signals fired: $fired | Blocked: $blocked"
```

---

## Part 7: Reading the Daily Report

Run the report script at end of session (or anytime):

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_daily_signal_report.py
```

For a specific date:

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_daily_signal_report.py `
    --date 2026-06-03
```

Send to Telegram after generating:

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_daily_signal_report.py `
    --date 2026-06-03 --send-telegram
```

### Report Metrics Explained

| Metric | Description |
|--------|-------------|
| Signals Fired | Total signals that passed all blockers |
| Signals Blocked | Signals suppressed (news, stale bar, cooldown) |
| Block breakdown | Count by block reason (NEWS, STALE_BAR, cooldown) |
| Hypo PnL | Theoretical P&L if all fired signals were taken — assumes fill at entry_ref and immediate exit at target or stop |
| Win Rate | % of resolved hypothetical signals that hit target |
| Avg R | Average R multiple achieved (1.0 = 1R profit, -1.0 = 1R loss) |
| Worst Miss | The best blocked signal — shows what you missed due to blocking |

The written JSON report is at:
```
06_live_trading/reports/daily_YYYYMMDD.json
```

---

## Part 8: Troubleshooting

### Problem: "No signal records found" in daily report

**Cause:** The engine never ran, ran in `--dry-run` mode, or ran with `--no-write`.

**Fix:** Confirm the engine ran in normal (not dry-run) mode. Check that the log file exists:
```powershell
Test-Path "C:\Users\conor\Desktop\quant-research\06_live_trading\logs\signals_$(Get-Date -Format 'yyyyMMdd').jsonl"
```

### Problem: All signals show [STALE_BAR]

**Cause:** The bar builder stopped or the parquet files have not been updated.

**Fix:**
1. Check file modification times: `Get-Item 01_data/tick_bars/GC_bars_l2_1m.parquet | Select-Object LastWriteTime`
2. Restart the bar builder (tick_bar_builder.py or tick_bar_builder_databento.py)
3. Use `--stale-threshold-minutes 60` as a temporary override if you know the data is valid but older

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 `
    C:\Users\conor\Desktop\quant-research\04_codebase\tick_manual_signal_engine.py `
    --watch --stale-threshold-minutes 60
```

### Problem: "Strategy import failed" warning at startup

**Cause:** The L2 strategy files in `src/strategies/` are not importable — usually a missing dependency or Python path issue.

**Fix:**
1. Activate the venv: `C:\Users\conor\Desktop\quant-research\venv_new\Scripts\Activate.ps1`
2. Run the import manually:
   ```powershell
   C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -c `
       "from src.strategies.l2_cvd_strategies import CVDMicropriceStrategy; print('OK')"
   ```
   If this fails, check the error message for the missing module.
3. The engine will fall back to stub strategies that never fire. This is safe but produces no signals.

### Problem: Missing L2 columns (no signals despite market activity)

**Cause:** The L2 parquet file exists but is missing columns like `cvd_delta`, `buy_sweeps`, or `microprice_last`. This happens when the bar builder runs in standard OHLCV mode without L2 features.

**Check which columns are present:**
```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -c "
import pandas as pd
df = pd.read_parquet(r'C:\Users\conor\Desktop\quant-research\01_data\tick_bars\GC_bars_l2_1m.parquet')
print(list(df.columns))
"
```

**Expected L2 columns:** ofi_1, ofi_5, imbal_L5_last, imbal_L5_mean, microprice_last, microprice_mean, buy_sweeps, sell_sweeps, net_sweeps, sweep_net_size, absorption_score, session_vwap, cvd, cvd_delta, buy_vol, sell_vol

If these are missing, the bar builder needs to be run in L2 mode. Refer to `tick_bar_builder_databento.py` and the L2 feature engine documentation.

### Problem: Telegram alerts not arriving

**Step 1:** Verify environment variables are set in the same PowerShell session:
```powershell
$env:TELEGRAM_BOT_TOKEN
$env:TELEGRAM_CHAT_ID
```
Both should print non-empty values.

**Step 2:** Test the bot token directly:
```powershell
$token = $env:TELEGRAM_BOT_TOKEN
$chat  = $env:TELEGRAM_CHAT_ID
Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" `
    -Method Post -Body @{ chat_id=$chat; text="test" } -ContentType "application/json"
```

**Step 3:** Check that `requests` is installed in the venv:
```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -c "import requests; print(requests.__version__)"
```

**Step 4:** Look for "Telegram enabled but ... not set" warnings in the engine startup output. This indicates the env vars were not found when the engine started.

### Problem: Too many signals (cooldown not working)

**Cause:** The cooldown counter is per-engine-instance. If you restart the engine, the cooldown resets. Also, the cooldown is in *pass iterations*, not clock time — at 60s interval, 30 bars = 30 minutes.

**Fix:** If you want a longer cooldown, increase `SIGNAL_COOLDOWN_BARS` in `tick_manual_signal_engine.py` (line with `SIGNAL_COOLDOWN_BARS = 30`).

### Problem: Engine crashes with "ModuleNotFoundError: No module named 'zoneinfo'"

**Cause:** Python version < 3.9. `zoneinfo` is built into Python 3.9+.

**Fix:**
```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe --version
```
If below 3.9, either upgrade Python or install `tzdata` and replace `from zoneinfo import ZoneInfo` with `from backports.zoneinfo import ZoneInfo` (requires `pip install backports.zoneinfo`).

### Problem: "Insufficient bars" for a symbol

**Cause:** Bar file has fewer than 60 rows. This happens on new files or near market open.

**Fix:** Wait for more bars to accumulate (60 minutes at 1m bars). The engine will automatically start processing once the threshold is met on the next pass.

---

## Quick Reference Card

```
START ENGINE (watch mode):
  python -X utf8 04_codebase/tick_manual_signal_engine.py --watch --symbols SI GC --telegram

ONE PASS ONLY:
  python -X utf8 04_codebase/tick_manual_signal_engine.py --once

DRY RUN (test, no logging):
  python -X utf8 04_codebase/tick_manual_signal_engine.py --dry-run --once

DAILY REPORT (today):
  python -X utf8 04_codebase/tick_daily_signal_report.py

DAILY REPORT (specific date + telegram):
  python -X utf8 04_codebase/tick_daily_signal_report.py --date 2026-06-03 --send-telegram

LOG FILE LOCATION:
  06_live_trading/logs/signals_YYYYMMDD.jsonl

REPORT FILE LOCATION:
  06_live_trading/reports/daily_YYYYMMDD.json
```

All commands assume working directory is `C:\Users\conor\Desktop\quant-research`. Prefix with the full Python path if running from elsewhere.
