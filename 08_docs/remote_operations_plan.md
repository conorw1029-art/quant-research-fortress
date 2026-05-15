# Remote Operations Plan — Quant Research Fortress

## Connection

The fortress is accessible via **Tailscale + OpenSSH**.

| Setting | Value |
|---------|-------|
| Tailscale IP | `100.78.208.74` |
| SSH username | `Conor` |
| SSH command | `ssh Conor@100.78.208.74` |

Tailscale must be running on both your phone/laptop and the fortress PC.
No router port-forwarding is needed — Tailscale handles the tunnel.

---

## First-Time Setup (phone / remote laptop)

1. Install Tailscale on your device and sign in with the same account.
2. Confirm the fortress appears in your Tailscale device list as `100.78.208.74`.
3. SSH in: `ssh Conor@100.78.208.74`
4. Once connected, navigate to the codebase: `cd C:\Users\conor\Desktop\quant-research\04_codebase`

---

## Key Paths

| Purpose | Path |
|---------|------|
| Project root | `C:\Users\conor\Desktop\quant-research\` |
| Codebase (working dir) | `C:\Users\conor\Desktop\quant-research\04_codebase\` |
| Python executable | `C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe` |
| Zoo database | `C:\Users\conor\Desktop\quant-research\05_backtests\zoo.jsonl` |
| Batch results | `C:\Users\conor\Desktop\quant-research\05_backtests\batch*_results.jsonl` |
| Logs | `C:\Users\conor\Desktop\quant-research\05_backtests\logs\` |
| Scripts | `C:\Users\conor\Desktop\quant-research\scripts\` |

All Python commands must use `venv_new`, not the old `venv`.

---

## Remote Scripts

Run these from PowerShell after SSH-ing in.
Navigate to scripts folder first if needed: `cd C:\Users\conor\Desktop\quant-research`

### 1. Check Fortress Status (safe, read-only)

```powershell
.\scripts\check_fortress_status.ps1
```

Shows: timestamp, git status, Python processes, zoo record count, newest logs, last 40 lines of latest log, recent batch results.

### 2. Re-evaluate Zoo

```powershell
.\scripts\zoo_reevaluate.ps1
```

Runs `zoo_reevaluate.py` using `venv_new`. Logs output to `05_backtests/logs/zoo_reevaluate_TIMESTAMP.log`.

### 3. Commit Checkpoint

```powershell
.\scripts\commit_checkpoint.ps1
```

Shows git status, prompts for your commit message, then runs `git add -A`, `git commit`, `git push`. **Never auto-commits** — always requires your message.

### 4. Kill Fortress Python Jobs

```powershell
.\scripts\kill_fortress_python_jobs.ps1
```

Lists running Python processes with PID, memory, CPU, start time, and command line. Shows which are fortress-related. **Always asks confirmation before killing**. Never silently kills unrelated processes.

### 5. Run a Batch

```powershell
.\scripts\run_batch.ps1 stress_test_batch9.py
```

Runs any batch script by name. Logs everything to `05_backtests/logs/BATCHNAME_TIMESTAMP.log`. Pass the filename as an argument:

```powershell
.\scripts\run_batch.ps1 stress_test_batch9.py
.\scripts\run_batch.ps1 stress_test_batch10.py
.\scripts\run_batch.ps1 portfolio_backtest.py
```

---

## Running Batches Manually

```powershell
cd C:\Users\conor\Desktop\quant-research\04_codebase
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe stress_test_batch9.py
```

For long-running batches, use `Start-Job` or run via `run_batch.ps1` which keeps the session alive.

---

## Monitoring a Running Batch

```powershell
# Check if Python is running
Get-Process python

# Tail the latest batch result file as it grows
Get-Content C:\Users\conor\Desktop\quant-research\05_backtests\batch9_results.jsonl -Wait

# Or check the log if run via run_batch.ps1
Get-Content C:\Users\conor\Desktop\quant-research\05_backtests\logs\stress_test_batch9_*.log -Tail 20 -Wait
```

---

## Safety Rules

| Rule | Detail |
|------|--------|
| No public internet exposure | Tailscale only — do NOT open SSH on the router |
| No zoo record deletion | Never delete or edit records in `zoo.jsonl` manually |
| No live trading | No broker API calls, no order submission |
| No validation loosening | DSR >= 1.0, PF >= 1.25 thresholds are fixed |
| Always use `venv_new` | Do not use the old `venv` — it may have broken dependencies |
| Commit messages required | `commit_checkpoint.ps1` will never auto-commit |
| GitHub pushes need review | Run `git status` and review diffs before pushing |
