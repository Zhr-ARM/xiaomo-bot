param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Xiaomo QQ Bot.lnk"
$target = Join-Path $root "start_bot.vbs"

if ($Remove) {
    if (Test-Path $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Host "[Done] Removed $shortcutPath"
    } else {
        Write-Host "[Skip] Startup shortcut not found"
    }
    exit 0
}

if (-not (Test-Path $target)) {
    throw "Missing startup target: $target"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
$shortcut.Description = "Start Xiaomo QQ Bot"
$shortcut.Save()

Write-Host "[Done] Installed startup shortcut: $shortcutPath"
Write-Host "[Hint] Remove it with: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Remove"
