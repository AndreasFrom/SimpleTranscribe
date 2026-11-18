@echo off
rem Runs the test suite (about 30 seconds).
rem Uses the .venv made by setup.bat, else %USERPROFILE%\.venvs\annotator, else Python on PATH.
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\annotator\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m unittest discover -s tests -v
