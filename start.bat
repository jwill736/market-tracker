@echo off
rem Plumbline on your own computer (Windows): double-click start.bat
rem First run: creates a Python environment, installs the app and a .env file. Then opens the dashboard.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo First run: setting up, a minute or two...
  %PY% -m venv .venv || (echo Plumbline needs Python 3.11 or newer from python.org & pause & exit /b 1)
  .venv\Scripts\python -m pip install -q --upgrade pip
)
.venv\Scripts\python -m pip install -q -e .
if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Put your email in SEC_USER_AGENT there.
)
git pull --ff-only -q 2>nul
.venv\Scripts\mt serve --open
pause
