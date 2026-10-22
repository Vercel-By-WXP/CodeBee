@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "PYTHONIOENCODING=utf-8"
rem CodeBee service launcher: clear the port (kill by PID only), then start.
rem Usage: start-service.bat [dev]  -- dev = run THIS repo code; default = npm
rem installed copy (daily production entry). Extra args pass through to main.py,
rem e.g.: start-service.bat dev --port 8765
rem
rem ASCII-ONLY ON PURPOSE: on this machine cmd mis-parses multibyte batch lines
rem (byte-offset drift makes cmd execute line fragments as commands; real run
rem 2026-10-22 executed a "CodeBee ..." fragment out of the Chinese closing-hint
rem line, which resolved to the npm codebee shim, got rejected by its arg
rem whitelist (exit 2) and failed the whole launcher). Every Chinese hint below
rem is printed at runtime: ASCII unicode escapes decoded via unicode_escape -
rem same pattern as the Desktop "codebee sync+restart" cmd.
rem Do NOT type non-ASCII into this file.
cd /d "%~dp0"

set "PORT=8765"
if defined CB_PORT set "PORT=%CB_PORT%"

rem python must exist before we can print any hint through it
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3.8+ first.
  pause
  exit /b 1
)

rem dev switches the code dir only; it is not passed through to main.py
set "EXTRA=%*"
set "APPDIR="
if "%~1"=="dev" (
  set "APPDIR=%~dp0"
  set "EXTRA="
) else (
  for /f "delims=" %%i in ('npm root -g 2^>nul') do set "APPDIR=%%i\codebee"
)
if not exist "!APPDIR!\app\main.py" set "APPDIR=D:\nvm\v24.19.0\node_modules\codebee"
if not exist "!APPDIR!\app\main.py" (
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "[\u9519\u8bef] \u627e\u4e0d\u5230 CodeBee \u4ee3\u7801\uff1a{0}" "!APPDIR!\app\main.py"
  pause
  exit /b 1
)

rem ---------- port cleanup: kill by PID only (never by image name) ----------
set "KILLED="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "[CodeBee] \u7aef\u53e3 {0} \u88ab PID {1} \u5360\u7528\uff0c\u7ed3\u675f\u5b83..." "%PORT%" "%%p"
  taskkill /F /T /PID %%p >nul 2>nul
  set "KILLED=1"
)
if defined KILLED (
  timeout /t 2 /nobreak >nul
  netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>nul && (
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "[\u8b66\u544a] \u7aef\u53e3 {0} \u4ecd\u6709\u76d1\u542c\uff0c\u8bf7\u624b\u52a8\u68c0\u67e5\uff1anetstat -ano | findstr :{1}" "%PORT%"
  )
)

rem ---------- start CodeBee ----------
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "[CodeBee] \u542f\u52a8\u670d\u52a1\uff1a{0} ..." "!APPDIR!"
start "CodeBee" /min /d "!APPDIR!" python app\main.py !EXTRA!

rem wait for the service to listen (up to 15 tries, readiness hint only;
rem netstat -ano alone can take seconds on this box, hence the loose budget)
set /a TRIES=0
:wait_up
timeout /t 1 /nobreak >nul
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>nul && goto :up
set /a TRIES+=1
if %TRIES% lss 15 goto :wait_up
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "[\u8b66\u544a] \u670d\u52a1 {0} \u79d2\u5185\u672a\u5728 {1} \u76d1\u542c\uff08\u9996\u6b21\u542f\u52a8\u53ef\u80fd\u8f83\u6162\uff09\u3002" "%TRIES%" "%PORT%"
:up

echo.
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "\u672c\u673a\u5730\u5740: http://localhost:{0}" "%PORT%"
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "\u4ee4\u724c\u89c1 CodeBee \u63a7\u5236\u53f0\uff0c\u6216\u672c\u673a\u6253\u5f00\u540e\u8bbe\u7f6e-\u624b\u673a\u8fde\u63a5\u91cc\u626b\u7801\u3002"
  python -c "import sys;print(sys.argv[1].encode('ascii').decode('unicode_escape').format(*sys.argv[2:]))" "\u5173\u95ed\uff1a\u7ed3\u675f CodeBee \u6700\u5c0f\u5316\u7a97\u53e3\u5373\u53ef"
timeout /t 5 >nul
endlocal
