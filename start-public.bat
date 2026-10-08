@echo off
chcp 65001 >nul
rem CodeBee 公网启动：端口清场（按 PID）+ CodeBee 服务（反代感知）+ Cloudflare Tunnel
rem 用法：start-public.bat [dev]   —— dev=起本仓库代码；默认起 npm 安装副本（日常生产入口）
rem 附加参数原样透传给 main.py，如：start-public.bat dev --port 8765
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PORT=8765"
if defined CB_PORT set "PORT=%CB_PORT%"
set "PUBURL=https://codebee.fixwikihub.com"
set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
if not exist "%CF%" set "CF=cloudflared"

rem dev 参数只切代码目录，不透传给 main.py
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
  echo [错误] 找不到 CodeBee 代码：!APPDIR!\app\main.py
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.8+
  pause
  exit /b 1
)

rem ---------- 端口清场：只按 PID 杀（绝不按映像名，防连坐并行服务） ----------
set "KILLED="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  echo [CodeBee] 端口 %PORT% 被 PID %%p 占用，结束它...
  taskkill /F /T /PID %%p >nul 2>nul
  set "KILLED=1"
)
if defined KILLED (
  timeout /t 2 /nobreak >nul
  netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>nul && (
    echo [警告] 端口 %PORT% 仍有监听，请手动检查：netstat -ano ^| findstr :%PORT%
  )
)

rem ---------- 启动 CodeBee ----------
echo [CodeBee] 启动服务：!APPDIR!（反代回源强制校验令牌）...
start "CodeBee" /min /d "!APPDIR!" python app\main.py --trusted-proxy --public-url "%PUBURL%" !EXTRA!

rem 等服务监听就绪再拉隧道（最多 15 秒）
set /a TRIES=0
:wait_up
timeout /t 1 /nobreak >nul
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>nul && goto :up
set /a TRIES+=1
if %TRIES% lss 15 goto :wait_up
echo [警告] 服务 %TRIES% 秒内未在 %PORT% 监听（首次启动可能较慢），仍继续启动隧道...
:up

rem ---------- 启动 Cloudflare Tunnel ----------
echo [CodeBee] 启动 Cloudflare Tunnel（codebee.fixwikihub.com）...
start "cloudflared-codebee" /min "%CF%" tunnel --config "%USERPROFILE%\.cloudflared\config-codebee.yml" run
echo.
echo 公网地址: %PUBURL%   本机地址: http://localhost:%PORT%
echo 令牌见 CodeBee 控制台，或本机打开后设置-手机连接里扫码。
echo 关闭：结束 "CodeBee" 与 "cloudflared-codebee" 两个最小化窗口即可。
timeout /t 5 >nul
endlocal
