@echo off
setlocal
set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
  echo Chrome not found at:
  echo %CHROME_EXE%
  exit /b 1
)
start "" "%CHROME_EXE%" --remote-debugging-port=9222
echo Chrome started with --remote-debugging-port=9222
