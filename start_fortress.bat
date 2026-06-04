@echo off
:: ============================================================
:: start_fortress.bat — Launch the full Fortress trading system
:: ============================================================
:: Double-click this file to start everything.
::
:: What it does:
::   Window 1: tick_live_bar_reader.py  — reads NinjaTrader JSONL bars
::             and appends them to parquet files every 30 seconds
::   Window 2: tick_live_executor.py --mock --poll 60
::             — runs all 44 strategies, sends Telegram alerts
::
:: To use NinjaTrader instead of mock:
::   Change --mock to --ninjatrader --nt-account Sim101
::
:: To set credentials, edit the SET lines below.
:: ============================================================

:: ── Telegram credentials ──────────────────────────────────────────────────────
SET TELEGRAM_BOT_TOKEN=8034600379:AAGLzv9sFl61fya5DBkeTcidxvrd9o1aLmA
SET TELEGRAM_CHAT_ID=8483433910

:: ── Tradovate credentials (fill in when ready) ───────────────────────────────
:: SET TRADOVATE_USERNAME=your@email.com
:: SET TRADOVATE_PASSWORD=yourpassword
:: SET TRADOVATE_CID=12345
:: SET TRADOVATE_SECRET=yoursecret

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

:: ── Window 1: Live bar reader (NinjaTrader JSONL -> parquet) ─────────────────
start "Fortress LiveReader" cmd /k "cd /d %CODE% && python tick_live_bar_reader.py --interval 30 --verbose"

:: Small delay so the reader starts first
timeout /t 3 /nobreak >nul

:: ── Window 2: Strategy executor ──────────────────────────────────────────────
start "Fortress Executor" cmd /k "cd /d %CODE% && python tick_live_executor.py --mock --poll 60"

echo.
echo  Both processes started. Check your Telegram for the startup message.
echo  Close either window to stop that process.
echo.
pause
