@echo off
chcp 65001 >nul
set BROWSER_MODE=attach
set CHROME_CDP_URL=http://127.0.0.1:9222
.venv\Scripts\python.exe -X utf8 main.py
