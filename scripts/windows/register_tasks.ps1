# CityLand 9 - register the Windows scheduled tasks on the SERVER PC.
#
#   Run in PowerShell "as Administrator", from the project folder:
#     powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1
#     powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -BackupTime 21:00
#     powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -Remove
#
# Creates two tasks (runs as SYSTEM: starts at boot without anyone signing in, no password stored):
#   "CityLand9 Server"  at startup -> backend\service.py (keeps the web server running, restarts it)
#   "CityLand9 Backup"  daily      -> database\backup.py (compressed dump, retention, status for /readyz)
# Both are re-created if they already exist. Nothing here needs internet access.
param(
    [string]$BackupTime = "20:00",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Run this in PowerShell opened with 'Run as administrator'." -ForegroundColor Red
    exit 1
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$names = @("CityLand9 Server", "CityLand9 Backup")

foreach ($name in $names) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed existing task: $name"
    }
}
if ($Remove) { Write-Host "Done (tasks removed)."; exit 0 }

if (-not (Test-Path $python)) {
    Write-Host "Not found: $python  - run START_WINDOWS.bat once first (it creates .venv)." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "Not found: .env  - complete docs\run-guide\02-DATABASE_SETUP.md first." -ForegroundColor Red
    exit 1
}

$system = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Server: at boot, never time out, restart the supervisor itself if it ever fails (3 times, 1 minute apart).
$serverAction = New-ScheduledTaskAction -Execute $python -Argument "`"$root\backend\service.py`"" -WorkingDirectory $root
$serverTrigger = New-ScheduledTaskTrigger -AtStartup
$serverTrigger.Delay = "PT30S"   # let the network come up first
$serverSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $names[0] -Action $serverAction -Trigger $serverTrigger -Principal $system `
    -Settings $serverSettings -Description "CityLand 9 web server (backend\service.py). Stop with STOP_WINDOWS.bat." | Out-Null
Write-Host "Registered: $($names[0]) (at startup)"

# Backup: daily; if the PC was off at that time, run as soon as it is back on. 2-hour limit.
$backupAction = New-ScheduledTaskAction -Execute $python -Argument "`"$root\database\backup.py`"" -WorkingDirectory $root
$backupTrigger = New-ScheduledTaskTrigger -Daily -At $BackupTime
$backupSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $names[1] -Action $backupAction -Trigger $backupTrigger -Principal $system `
    -Settings $backupSettings -Description "CityLand 9 daily database backup (database\backup.py). Log: logs\backup.log" | Out-Null
Write-Host "Registered: $($names[1]) (daily at $BackupTime)"

# The SYSTEM account must be able to write the logs and backups (it normally can; make sure).
foreach ($dir in @("logs", "database\backups")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $dir) | Out-Null
}

Write-Host ""
Write-Host "Start the server now (or restart the PC):  Start-ScheduledTask -TaskName 'CityLand9 Server'"
Write-Host "Test a backup now:                         Start-ScheduledTask -TaskName 'CityLand9 Backup'"
Write-Host "Then check:  logs\service.log, logs\backup.log, http://127.0.0.1:5000/readyz"
