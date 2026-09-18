@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project virtual environment is missing. Run setup.bat first.
  exit /b 2
)
".venv\Scripts\python.exe" run_theory_audit.py
if errorlevel 1 exit /b 21
".venv\Scripts\python.exe" analyze_gain_decomposition.py
if errorlevel 1 exit /b 22
echo.
echo [neo] Theory audit and gain decomposition complete.
exit /b 0
