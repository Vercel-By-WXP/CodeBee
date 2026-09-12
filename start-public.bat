@echo off
rem Tutti 公网启动：Tutti（反代感知模式）+ Cloudflare Tunnel
rem 前置：cloudflared 已登录（~/.cloudflared/cert.pem）、config-tutti.yml 已配置
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.8+
  pause
  exit /b 1
)
where cloudflared >nul 2>nul
if errorlevel 1 set "cloudflared=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"

echo [Tutti] 启动服务（反代回源强制校验令牌）...
start "Tutti" /min python app\main.py --trusted-proxy --public-url "https://tutti.fixwikihub.com" %*
timeout /t 3 /nobreak >nul

echo [Tutti] 启动 Cloudflare Tunnel（tutti.fixwikihub.com）...
start "cloudflared-tutti" /min "%cloudflared%" tunnel --config "%USERPROFILE%\.cloudflared\config-tutti.yml" run
echo.
echo 公网地址: https://tutti.fixwikihub.com  （令牌见 Tutti 控制台，或本机打开后设置-手机连接里扫码）
echo 关闭：结束 "Tutti" 与 "cloudflared-tutti" 两个最小化窗口即可。
timeout /t 3 >nul
