# ============================================================
# Media Organizer — SAFE duplicate deletion script
# Generated: 2026-09-01 | Groups: 3,770 safe | Files: 4,039 | ~30.72 GB
# Review files_to_delete_safe.txt first. Then run:
#   powershell -ExecutionPolicy Bypass -File delete_duplicates.ps1
# Every deletion is logged to delete_log.txt with size.
# Recycle-safe tip: edit $UseRecycleBin = $true below to send to Recycle Bin
# instead of permanent deletion (recommended for first run).
# ============================================================

param(
    [string]$List = ""   # optional: path to a specific list, e.g. -List .\by_type\delete_video.txt
)

$UseRecycleBin = $false   # set $true to move to Recycle Bin instead of deleting

$listFile = if ($List -ne "") {
    if (Test-Path $List) { Resolve-Path $List } else { Write-Host "List not found: $List"; exit 1 }
} else {
    Join-Path $PSScriptRoot "delete_list_safe.txt"
}
$logFile  = Join-Path $PSScriptRoot "delete_log.txt"

Write-Host "Using list: $listFile"

# Guard: never touch the project folder or system paths
$guarded = @('H:\projects\folder_organizer', 'C:\Windows', 'C:\Program Files')

$totalBytes = 0
$okCount = 0
$failCount = 0

Add-Content $logFile "===== RUN START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') UseRecycleBin=$UseRecycleBin ====="

Get-Content $listFile -Encoding UTF8 | Where-Object { $_.Trim() -ne '' -and -not $_.TrimStart().StartsWith('#') } | ForEach-Object {
    $p = $_.Trim()
    try {
        if (-not (Test-Path -LiteralPath $p)) {
            Add-Content $logFile "SKIP-missing`t$p"
            return
        }
        foreach ($g in $guarded) {
            if ($p -like "$g*") {
                Add-Content $logFile "SKIP-guarded`t$p"
                return
            }
        }
        $size = (Get-Item -LiteralPath $p).Length
        if ($UseRecycleBin) {
            Add-Type -AssemblyName Microsoft.VisualBasic
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
        }
        else {
            Remove-Item -LiteralPath $p -Force
        }
        Add-Content $logFile "DELETED`t$size`t$p"
        $totalBytes += $size
        $okCount++
    }
    catch {
        Add-Content $logFile "FAIL`t$p`t$($_.Exception.Message)"
        $failCount++
    }
}

$gb = [math]::Round($totalBytes / 1GB, 2)
Add-Content $logFile "===== RUN END: deleted=$okCount failed=$failCount freed=$gb GB ====="
Write-Host "Done. Deleted: $okCount  Failed: $failCount  Freed: $gb GB"
Write-Host "Log: $logFile"
