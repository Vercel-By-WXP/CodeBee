@echo off
rem Tutti multi-agent orchestration launcher (ASCII-only: cmd mis-parses
rem UTF-8 CJK lines under GBK codepages -- see start-service.bat header).
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found. Install Python 3.8+ first.
  pause
  exit /b 1
)
python app\main.py %*
if errorlevel 1 pause
