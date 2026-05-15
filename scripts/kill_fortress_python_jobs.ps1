# ============================================================
# kill_fortress_python_jobs.ps1
# Lists running Python processes. Asks confirmation before killing.
# Never silently kills. Prefers fortress-related processes only.
# ============================================================

$FORTRESS_PATH = "quant-research"

Write-Output "============================================================"
Write-Output "  FORTRESS PYTHON JOB MANAGER"
Write-Output "  Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "============================================================"

$pyProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue

if (-not $pyProcs) {
    Write-Output ""
    Write-Output "No python.exe processes are currently running."
    exit 0
}

Write-Output ""
Write-Output "Running Python processes:"
Write-Output ""

$fortressProcs = @()
$otherProcs    = @()

foreach ($p in $pyProcs) {
    $pid_  = $p.Id
    $mem   = [math]::Round($p.WorkingSet64 / 1MB, 1)
    $cpu   = [math]::Round($p.CPU, 1)
    $start = $p.StartTime

    # Try to get command line via WMI
    $cmdline = ""
    try {
        $wmi = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_" -ErrorAction SilentlyContinue
        if ($wmi) { $cmdline = $wmi.CommandLine }
    } catch {}

    $isFortress = $cmdline -like "*$FORTRESS_PATH*"

    $info = "  PID=$pid_  CPU=${cpu}s  Mem=${mem}MB  Started=$start"
    if ($cmdline) { $info += "`n    CMD: $($cmdline.Substring(0, [Math]::Min($cmdline.Length, 140)))" }

    if ($isFortress) {
        $fortressProcs += $p
        Write-Output "[FORTRESS] $info"
    } else {
        $otherProcs += $p
        Write-Output "[OTHER   ] $info"
    }
    Write-Output ""
}

Write-Output "------------------------------------------------------------"
Write-Output "  Fortress processes : $($fortressProcs.Count)"
Write-Output "  Other processes    : $($otherProcs.Count)"
Write-Output "------------------------------------------------------------"

if ($fortressProcs.Count -eq 0) {
    Write-Output ""
    Write-Output "No fortress Python processes detected. Nothing to kill."
    exit 0
}

Write-Output ""
$confirm = Read-Host "Kill ALL $($fortressProcs.Count) fortress Python process(es)? Type YES to confirm"

if ($confirm -ne "YES") {
    Write-Output "CANCELLED — no processes killed."
    exit 0
}

foreach ($p in $fortressProcs) {
    Write-Output "Killing PID $($p.Id)..."
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}

Write-Output "Done. Killed $($fortressProcs.Count) process(es)."
