@echo off
rem Tutti 多智能体编排台启动脚本
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.8+
  pause
  exit /b 1
)
python app\main.py %*
if errorlevel 1 pause
