# ============================================================
# check_fortress_status.ps1
# Safe read-only status check for the Quant Research Fortress.
# Run from anywhere. No writes, no kills, no side effects.
# ============================================================

$FORTRESS = "C:\Users\conor\Desktop\quant-research"
$CODEBASE = "$FORTRESS\04_codebase"
$BACKTESTS = "$FORTRESS\05_backtests"
$LOGS      = "$BACKTESTS\logs"
$ZOO       = "$BACKTESTS\zoo.jsonl"
$PYTHON    = "$FORTRESS\venv_new\Scripts\python.exe"

Write-Output ""
Write-Output "============================================================"
Write-Output "  FORTRESS STATUS CHECK"
Write-Output "  Timestamp : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "  Host      : $env:COMPUTERNAME"
Write-Output "============================================================"

# --- Working dirs ---
Write-Output ""
Write-Output "--- PATHS ---"
Write-Output "  Codebase  : $CODEBASE"
Write-Output "  Python    : $PYTHON  [exists=$(Test-Path $PYTHON)]"

# --- Git status ---
Write-Output ""
Write-Output "--- GIT ---"
Push-Location $FORTRESS
$branch = git rev-parse --abbrev-ref HEAD 2>$null
$status = git status --short 2>$null
$lastCommit = git log -1 --oneline 2>$null
Write-Output "  Branch    : $branch"
Write-Output "  Last commit: $lastCommit"
if ($status) {
    Write-Output "  Dirty files:"
    $status | ForEach-Object { Write-Output "    $_" }
} else {
    Write-Output "  Working tree: CLEAN"
}
Pop-Location

# --- Python processes ---
Write-Output ""
Write-Output "--- PYTHON PROCESSES ---"
$pyProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue
if ($pyProcs) {
    $pyProcs | ForEach-Object {
        $id   = $_.Id
        $mem  = [math]::Round($_.WorkingSet64 / 1MB, 1)
        $cpu  = [math]::Round($_.CPU, 1)
        $start = $_.StartTime
        Write-Output "  PID $id  CPU=${cpu}s  Mem=${mem}MB  Started=$start"
    }
} else {
    Write-Output "  No python.exe processes running."
}

# --- PowerShell processes hinting at fortress jobs ---
Write-Output ""
Write-Output "--- POWERSHELL JOBS (fortress-related) ---"
$psProcs = Get-Process -Name "powershell","pwsh" -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID }
if ($psProcs) {
    $psProcs | ForEach-Object {
        Write-Output "  PID $($_.Id)  Started=$($_.StartTime)"
    }
} else {
    Write-Output "  No other PowerShell sessions."
}

# --- Zoo database ---
Write-Output ""
Write-Output "--- ZOO DATABASE ---"
if (Test-Path $ZOO) {
    $info = Get-Item $ZOO
    $sizeMB = [math]::Round($info.Length / 1KB, 1)
    Write-Output "  File   : $ZOO"
    Write-Output "  Size   : ${sizeMB} KB"
    Write-Output "  Modified: $($info.LastWriteTime)"
    $lines = (Get-Content $ZOO | Measure-Object -Line).Lines
    $passCount = (Get-Content $ZOO | ForEach-Object { ($_ | ConvertFrom-Json).verdict } | Where-Object { $_ -eq "PASS" }).Count
    Write-Output "  Records: $lines total  |  PASS: $passCount"
} else {
    Write-Output "  NOT FOUND: $ZOO"
}

# --- Log files ---
Write-Output ""
Write-Output "--- LOG FILES (newest 10) ---"
if (Test-Path $LOGS) {
    $logFiles = Get-ChildItem $LOGS | Sort-Object LastWriteTime -Descending | Select-Object -First 10
    if ($logFiles) {
        $logFiles | ForEach-Object {
            $kb = [math]::Round($_.Length / 1KB, 1)
            Write-Output "  $($_.Name)  (${kb}KB  $($_.LastWriteTime))"
        }
        # Show last 40 lines of newest log
        $newest = $logFiles | Select-Object -First 1
        Write-Output ""
        Write-Output "--- TAIL: $($newest.Name) ---"
        Get-Content "$LOGS\$($newest.Name)" -Tail 40
    } else {
        Write-Output "  Logs folder is empty."
    }
} else {
    Write-Output "  Logs folder not found: $LOGS"
}

# --- Recent batch results ---
Write-Output ""
Write-Output "--- RECENT BATCH RESULTS ---"
Get-ChildItem "$BACKTESTS\batch*.jsonl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 8 |
    ForEach-Object {
        $kb = [math]::Round($_.Length / 1KB, 1)
        Write-Output "  $($_.Name)  (${kb}KB  $($_.LastWriteTime))"
    }

Write-Output ""
Write-Output "============================================================"
Write-Output "  END OF STATUS CHECK"
Write-Output "============================================================"
