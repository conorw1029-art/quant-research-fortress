# ============================================================
# commit_checkpoint.ps1
# Interactive git checkpoint commit. Always prompts for message.
# Never auto-commits. Runs git add -A, commit, push.
# ============================================================

$FORTRESS = "C:\Users\conor\Desktop\quant-research"

Set-Location $FORTRESS

Write-Output "============================================================"
Write-Output "  GIT CHECKPOINT COMMIT"
Write-Output "  Repo: $FORTRESS"
Write-Output "============================================================"

# Show current status
Write-Output ""
Write-Output "--- GIT STATUS ---"
git status
Write-Output ""

# Show what would be staged
Write-Output "--- FILES THAT WILL BE STAGED (git add -A) ---"
git status --short
Write-Output ""

# Prompt for commit message (no auto-commit)
$msg = Read-Host "Enter commit message (or blank to CANCEL)"

if ([string]::IsNullOrWhiteSpace($msg)) {
    Write-Output "CANCELLED — no commit made."
    exit 0
}

Write-Output ""
Write-Output "Staging all changes..."
git add -A

Write-Output "Committing: $msg"
git commit -m $msg

Write-Output "Pushing to remote..."
git push

Write-Output ""
Write-Output "Done. Last commit:"
git log -1 --oneline
