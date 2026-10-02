@echo off
rem ===========================================================================
rem  CityLand 9 - stop the server and CityLand's own database (end of day).
rem    STOP_WINDOWS.bat           stop the web server and the database
rem    STOP_WINDOWS.bat --keep-db stop only the web server
rem  Only CityLand's processes are stopped (backend\run.py and its MariaDB on
rem  port 3307). XAMPP's own MySQL and other programs are not touched.
rem ===========================================================================
setlocal
cd /d "%~dp0"

echo Stopping the CityLand 9 web server...
rem When installed as a scheduled task (scripts\windows\register_tasks.ps1), end the task and its
rem supervisor first, otherwise the supervisor would start the server again.
schtasks /Query /TN "CityLand9 Server" >nul 2>&1 && schtasks /End /TN "CityLand9 Server" >nul 2>&1
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*backend*service.py*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host '  stopped the service supervisor' }"
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*backend*run.py*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host ('  stopped ' + @($p).Count + ' process(es)') } else { Write-Host '  the web server was not running' }"

if /i "%~1"=="--keep-db" goto :done
echo Stopping the CityLand 9 database...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" database\local_mysql.py stop
) else (
    echo   .venv not found; nothing to stop.
)

:done
echo Done.
rem short pause so the messages can be read (works without a console, unlike timeout)
ping -n 4 127.0.0.1 >nul
