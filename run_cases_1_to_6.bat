@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [neo] ERROR: .venv is missing. Run setup.bat first.
  exit /b 20
)
"%PY%" run_cases_1_to_6.py --replace
if errorlevel 1 exit /b 21
echo.
echo [neo] Results: %CD%\results\case1_to_6
exit /b 0
