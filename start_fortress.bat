@echo off
:: ============================================================
:: start_fortress.bat — Launch the full Fortress trading system
:: ============================================================
:: Double-click this file to start everything.
::
:: What it does:
::   Window 1: tick_tradovate_live_feed.py
::             Connects to Tradovate WebSocket, streams DOM + tape,
::             writes JSONL bar files (GC/SI/ES/NQ) with full L2 data.
::
::   Window 2: tick_live_bar_reader.py
::             Reads JSONL files, appends new bars to parquets
::             (including GC_bars_l2_1m.parquet for V10 strategies).
::
::   Window 3: tick_live_executor.py --mock --poll 60
::             Runs all 44 strategies, sends signals to Telegram.
::             Change --mock to nothing (no flag) when credentials are set.
::
:: ── Telegram credentials ──────────────────────────────────────────────────────
SET TELEGRAM_BOT_TOKEN=8034600379:AAGLzv9sFl61fya5DBkeTcidxvrd9o1aLmA
SET TELEGRAM_CHAT_ID=8483433910

:: ── Tradovate credentials ─────────────────────────────────────────────────────
:: Fill these in — same account you log into Tradovate / Lucid with.
:: TV_CID and TV_SECRET come from the Tradovate developer portal (API key).
SET TV_USERNAME=your@email.com
SET TV_PASSWORD=yourpassword
SET TV_APP_ID=FortressFeed
SET TV_APP_VER=1.0
SET TV_CID=0
SET TV_SECRET=

:: ── Path setup ────────────────────────────────────────────────────────────────
SET ROOT=%~dp0
SET CODE=%ROOT%04_codebase

:: ── Activate Python venv if it exists ────────────────────────────────────────
IF EXIST "%ROOT%venv\Scripts\activate.bat" (
    CALL "%ROOT%venv\Scripts\activate.bat"
) ELSE IF EXIST "%ROOT%.venv\Scripts\activate.bat" (
    CALL "%ROOT%.venv\Scripts\activate.bat"
)

echo.
echo  Starting Fortress Trading System...
echo.

:: ── Window 1: Yahoo Finance bar updater (GC, SI, ES, NQ — every 5 min) ───────
start "Fortress YFinance" cmd /k "cd /d %CODE% && python -X utf8 tick_yfinance_updater.py --loop --interval 300"

:: Small delay so first update completes before bar reader starts
timeout /t 10 /nobreak >nul

:: ── Window 2: Live bar reader (NinjaTrader JSONL -> parquet, every 30s) ──────
start "Fortress LiveReader" cmd /k "cd /d %CODE% && python -X utf8 tick_live_bar_reader.py --interval 30 --verbose"

:: Small delay so parquets are fresh before executor starts
timeout /t 3 /nobreak >nul

:: ── Window 3: Strategy executor ──────────────────────────────────────────────
start "Fortress Executor" cmd /k "cd /d %CODE% && python -X utf8 tick_live_executor.py --mock --poll 60"

:: Small delay before starting syncer
timeout /t 3 /nobreak >nul

:: ── Window 4: NT8 JSONL syncer (uploads L2 files to Hetzner server) ──────────
:: Only runs if NinjaTrader is writing JSONL files.
:: Requires SSH key at C:\Users\Conor\.ssh\fortress_deploy
start "Fortress NT8 Syncer" cmd /k "cd /d %CODE% && python -X utf8 tick_nt8_syncer.py --interval 30 --verbose"

echo.
echo  Four windows started:
echo    Fortress YFinance   — GC/SI/ES/NQ bars from Yahoo (free, 15min delayed)
echo    Fortress LiveReader — NinjaTrader JSONL to parquet converter
echo    Fortress Executor   — 44 strategies, Telegram alerts
echo    Fortress NT8 Syncer — uploads L2 data to Hetzner server (needs NT8 running)
echo.
echo  Dashboard: http://46.225.110.190:5050
echo  Check your Telegram for the startup message.
echo  Close any window to stop that process.
echo.
pause
