@echo off
REM Start local llama-server with tuned args (32k ctx / single slot / 12 threads / no flash-attn).
REM Why: CatPawAI default launches with -c 1048576, which eats ~19GB RAM on a 32GB box
REM and gives no speed gain. Replace it with this script if you want the tuned profile.
setlocal
set EXE=C:\Users\HP\.echobird\llama-server\bin\llama-server.exe
set MODEL=D:\Models\Qwen3.5-4B-Q4_K_M.gguf

if not exist "%EXE%" (
  echo [ERR] llama-server.exe not found: %EXE%
  exit /b 1
)
if not exist "%MODEL%" (
  echo [ERR] model not found: %MODEL%
  exit /b 1
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":11534" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%p >nul 2>&1
)
timeout /t 1 >nul

start "llama-server" /b "%EXE%" -m "%MODEL%" --port 11534 --host 127.0.0.1 -ngl 0 -c 32768 --parallel 1 -t 12 -fa off

echo Started llama-server on http://127.0.0.1:11534
echo CatPawAI gateway stays on http://127.0.0.1:11434/v1
echo Args: -c 32768 --parallel 1 -t 12 -fa off
endlocal
