@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\pythonw.exe" "%~dp0ui.py"
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "%~dp0ui.py"
) else (
    echo Virtual environment not found. Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

if errorlevel 1 (
    echo nDisplay Merger exited with an error.
    pause
)
