@echo off
REM ASCII-only wrapper: runs the FileScope release self test.
setlocal
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0release_selftest.py" %*
endlocal
