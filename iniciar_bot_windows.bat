@echo off
title BOT 14/15 EN VIVO - 24/7
cd /d "%~dp0"
echo ========================================================
echo   BOT 14/15 EN VIVO (ALTA_CONFIANZA + MINUTO 45)
echo   MODO 24/7 ACTIVO - CANAL TELEGRAM CONFIGURADO
echo ========================================================
echo.
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Usando entorno de ejecucion local...
    "C:\Users\ASUS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" bot_live.py
) else (
    python bot_live.py
)
echo.
echo El proceso del bot se ha cerrado. Presione una tecla para salir.
pause
