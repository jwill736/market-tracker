@echo off
rem Plumbline on your own computer (Windows): double-click start.bat
rem First run: creates a Python environment, installs the app and asks two setup questions. Then opens the dashboard.
rem Every run: gets the latest version, reinstalls only when its requirements changed, and starts the app.
cd /d "%~dp0"
set PY=python
where py >nul 2>nul
if not errorlevel 1 set PY=py -3
rem The rest is one block: cmd reads it whole before running it, so the git pull can safely update this file.
rem It runs the app through python.exe, never mt.exe: a running mt.exe can't be replaced, and a reinstall
rem that can't replace it leaves the app uninstalled.
(
  if not exist .venv (
    echo First run: setting up, a minute or two...
    %PY% -m venv .venv
    if errorlevel 1 (
      echo Plumbline needs Python 3.11 or newer from python.org
      pause
      exit /b 1
    )
    .venv\Scripts\python -m pip install -q --upgrade pip
  )
  git pull --ff-only -q 2>nul
  fc /b pyproject.toml .venv\installed-pyproject.toml >nul 2>nul
  if errorlevel 1 (
    .venv\Scripts\python -m pip install -q -e .
    if errorlevel 1 (
      echo.
      echo Couldn't install the update. If Plumbline is running in another window, close that window
      echo or use Stop Plumbline in the Start menu, then double-click start.bat again.
      pause
      exit /b 1
    )
    copy /y pyproject.toml .venv\installed-pyproject.toml >nul
  )
  .venv\Scripts\python -m market_tracker.cli setup --if-needed
  .venv\Scripts\python -m market_tracker.cli serve --open --pidfile plumbline.pid
  pause
)
