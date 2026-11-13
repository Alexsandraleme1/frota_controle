@echo off
cd /d %~dp0
if not exist .venv py -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
set HOST=0.0.0.0
py app.py
pause
