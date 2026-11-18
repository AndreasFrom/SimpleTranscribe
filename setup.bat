@echo off
rem One-time setup on Windows: makes a virtual environment in .venv and installs
rem what the app needs (PySide6 and ffmpeg, about 250 MB). Then start it with run.bat.
cd /d "%~dp0"
where python >nul 2>nul || (
    echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during the installation.
    pause
    exit /b 1
)
python -m venv .venv || goto :error
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
echo.
echo Done. Start the app with run.bat
pause
exit /b 0

:error
echo.
echo Setup failed - see "Troubleshooting" in README.md.
pause
exit /b 1
