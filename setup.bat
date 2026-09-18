@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV_DIR=%CD%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

echo [neo] Project: %CD%
echo [neo] Virtual environment: %VENV_DIR%

if exist "%VENV_PY%" goto :install

if defined WUS_BASE_PYTHON (
  "%WUS_BASE_PYTHON%" -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] < (3,13) else 1)"
  if errorlevel 1 goto :bad_version
  "%WUS_BASE_PYTHON%" -m venv "%VENV_DIR%"
  if errorlevel 1 goto :venv_fail
  goto :install
)

set "UV_PYTHON="
for /f "delims=" %%P in ('uv python find 3.12 2^>nul') do set "UV_PYTHON=%%P"
if defined UV_PYTHON (
  "%UV_PYTHON%" -m venv "%VENV_DIR%"
  if errorlevel 1 goto :venv_fail
  goto :install
)

py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 -m venv "%VENV_DIR%"
  if errorlevel 1 goto :venv_fail
  goto :install
)

py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.11 -m venv "%VENV_DIR%"
  if errorlevel 1 goto :venv_fail
  goto :install
)

goto :python_missing

:install
"%VENV_PY%" -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] < (3,13) else 1)"
if errorlevel 1 goto :bad_version
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :install_fail
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :install_fail
"%VENV_PY%" -m pip install -e . --no-deps
if errorlevel 1 goto :install_fail
"%VENV_PY%" -m wus_next.cli validate --profile wus_next\profiles\sixg_case1_v1.json
if errorlevel 1 goto :validate_fail
echo.
echo [neo] SUCCESS. Run run_cases_1_to_6.bat.
exit /b 0

:python_missing
echo [neo] ERROR: Python 3.11 or 3.12 was not found.
exit /b 11
:bad_version
echo [neo] ERROR: Python must be 3.11 or 3.12.
exit /b 12
:venv_fail
echo [neo] ERROR: Failed to create .venv.
exit /b 13
:install_fail
echo [neo] ERROR: pip installation failed.
exit /b 14
:validate_fail
echo [neo] ERROR: profile validation failed.
exit /b 15
