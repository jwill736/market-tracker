@echo off
rem Plumbline on your own computer (Windows): double-click start.bat
rem First run: creates a Python environment, installs the app and asks two setup questions. Then opens the dashboard.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo First run: setting up, a minute or two...
  %PY% -m venv .venv || (echo Plumbline needs Python 3.11 or newer from python.org & pause & exit /b 1)
  .venv\Scripts\python -m pip install -q --upgrade pip
)
git pull --ff-only -q 2>nul
.venv\Scripts\python -m pip install -q -e .
.venv\Scripts\mt setup --if-needed
.venv\Scripts\mt serve --open
pause
