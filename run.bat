@echo off
cd /d "%~dp0"
echo Starting Autodesk Inventor 2025 + LLM Studio...
python main.py
if errorlevel 1 (
    echo.
    echo Application exited with error.
    pause
)
