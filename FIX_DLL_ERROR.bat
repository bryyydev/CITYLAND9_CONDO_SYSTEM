@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo CITYLAND 9 V10.63 - FIX SQLAlchemy DLL ERROR
echo ================================================
echo.
echo This fix replaces the blocked SQLAlchemy 2.x compiled

echo extension with SQLAlchemy 1.4.54, which uses the

echo compatible pure-Python fallback for this application.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv
  if errorlevel 1 goto fail
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto fail
python -m pip uninstall -y SQLAlchemy Flask-SQLAlchemy
python -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 goto fail
python -c "import sqlalchemy; print('SQLAlchemy:', sqlalchemy.__version__)"
if errorlevel 1 goto fail
python -c "from flask_sqlalchemy import SQLAlchemy; print('Flask-SQLAlchemy: OK')"
if errorlevel 1 goto fail
python -c "import sys; sys.path.insert(0, 'backend'); import legacy_app; print('Application import: OK')"
if errorlevel 1 goto fail
python -c "import sys; sys.path.insert(0, 'backend'); import legacy_app; legacy_app.init_db(); print('Database initialization: OK')"
if errorlevel 1 goto fail
echo.
echo ================================================
echo FIX COMPLETE - STARTING CITYLAND 9
echo ================================================
start "" "http://127.0.0.1:5000"
.venv\Scripts\python.exe backend\run.py --lan
exit /b 0
:fail
echo.
echo ================================================
echo FIX FAILED

echo Please send me a screenshot of the error shown above.
echo ================================================
pause
exit /b 1
