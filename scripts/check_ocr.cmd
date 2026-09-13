@echo off
REM ASCII-only helper: reports whether Tesseract can be found by FileScope.
setlocal
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0check_environment.py" %*
endlocal
