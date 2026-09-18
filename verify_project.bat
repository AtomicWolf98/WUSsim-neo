@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [neo] ERROR: .venv is missing. Run setup.bat first.
  exit /b 20
)
"%PY%" -m compileall -q wus_next run_cases_1_to_6.py verify_neo.py
if errorlevel 1 exit /b 21
"%PY%" -m wus_next.cli validate --profile wus_next\profiles\sixg_case1_v1.json
if errorlevel 1 exit /b 22
"%PY%" verify_neo.py
if errorlevel 1 exit /b 23
echo [neo] PASS
exit /b 0
