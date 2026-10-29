@echo off
REM Starts the customer feedback web form.
REM Customer form: http://127.0.0.1:5000/   Staff queue: http://127.0.0.1:5000/queue
cd /d "%~dp0"
python src\app.py %*
