@echo off
rem Starts the annotator.   Optional:  run.bat video.mp4 transcript.txt
rem Uses the .venv made by setup.bat, else %USERPROFILE%\.venvs\annotator, else Python on PATH.
set "PY=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\annotator\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=pythonw"
"%PY%" "%~dp0annotator.py" %*
