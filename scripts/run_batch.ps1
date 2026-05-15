# ============================================================
# run_batch.ps1
# Generic batch runner. Pass the batch script name as argument.
# Usage:  .\run_batch.ps1 stress_test_batch9.py
# Logs output to 05_backtests/logs/batchNAME_TIMESTAMP.log
# ============================================================
# TODO: Update the BATCH_SCRIPT variable below when targeting a
#       specific batch, OR pass the script name as an argument:
#         .\run_batch.ps1 stress_test_batch9.py
# ============================================================

param(
    [string]$BatchScript = "stress_test_batch9.py"   # default — override as needed
)

$FORTRESS = "C:\Users\conor\Desktop\quant-research"
$CODEBASE = "$FORTRESS\04_codebase"
$LOGS     = "$FORTRESS\05_backtests\logs"
$PYTHON   = "$FORTRESS\venv_new\Scripts\python.exe"
$SCRIPT   = "$CODEBASE\$BatchScript"
$TS       = Get-Date -Format "yyyyMMdd_HHmmss"
$STEM     = [System.IO.Path]::GetFileNameWithoutExtension($BatchScript)
$LOGFILE  = "$LOGS\${STEM}_$TS.log"

# Ensure logs folder exists
if (-not (Test-Path $LOGS)) { New-Item -ItemType Directory -Path $LOGS | Out-Null }

Write-Output "============================================================"
Write-Output "  BATCH RUNNER"
Write-Output "  Script  : $SCRIPT"
Write-Output "  Python  : $PYTHON"
Write-Output "  Log     : $LOGFILE"
Write-Output "  Started : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "============================================================"

if (-not (Test-Path $PYTHON)) {
    Write-Output "ERROR: Python not found at $PYTHON"
    exit 1
}
if (-not (Test-Path $SCRIPT)) {
    Write-Output "ERROR: Batch script not found at $SCRIPT"
    Write-Output "Available batch scripts:"
    Get-ChildItem "$CODEBASE\stress_test_batch*.py" | Select-Object -ExpandProperty Name
    exit 1
}

Set-Location $CODEBASE

"Batch run started $(Get-Date) — $BatchScript" | Out-File $LOGFILE -Encoding utf8
& $PYTHON $SCRIPT 2>&1 | Tee-Object -FilePath $LOGFILE -Append

Write-Output ""
Write-Output "Batch complete. Log: $LOGFILE"
