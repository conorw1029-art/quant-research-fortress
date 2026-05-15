# ============================================================
# zoo_reevaluate.ps1
# Re-runs zoo_reevaluate.py and logs all output with timestamp.
# ============================================================

$FORTRESS = "C:\Users\conor\Desktop\quant-research"
$CODEBASE = "$FORTRESS\04_codebase"
$LOGS     = "$FORTRESS\05_backtests\logs"
$PYTHON   = "$FORTRESS\venv_new\Scripts\python.exe"
$SCRIPT   = "$CODEBASE\zoo_reevaluate.py"
$TS       = Get-Date -Format "yyyyMMdd_HHmmss"
$LOGFILE  = "$LOGS\zoo_reevaluate_$TS.log"

# Ensure logs folder exists
if (-not (Test-Path $LOGS)) { New-Item -ItemType Directory -Path $LOGS | Out-Null }

Write-Output "============================================================"
Write-Output "  ZOO RE-EVALUATE"
Write-Output "  Started : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "  Log     : $LOGFILE"
Write-Output "============================================================"

if (-not (Test-Path $PYTHON)) {
    Write-Output "ERROR: Python not found at $PYTHON"
    exit 1
}
if (-not (Test-Path $SCRIPT)) {
    Write-Output "ERROR: Script not found at $SCRIPT"
    exit 1
}

Set-Location $CODEBASE

"Zoo re-evaluate started $(Get-Date)" | Out-File $LOGFILE -Encoding utf8
& $PYTHON $SCRIPT 2>&1 | Tee-Object -FilePath $LOGFILE -Append

Write-Output ""
Write-Output "Log saved to: $LOGFILE"
