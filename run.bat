@echo off
REM Runs the feedback classification + routing bot. Used directly or from UiPath (Start Process).
REM Usage: run.bat [input.csv]   -> exit code 0 = OK, 1 = failure
cd /d "%~dp0"
if "%~1"=="" (
  python src\main.py
) else (
  python src\main.py --input "%~1"
)
exit /b %ERRORLEVEL%
