@echo off
echo.
echo  ==========================================
echo   eBRAM - Stopping service...
echo  ==========================================
echo.

set PID=

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    set PID=%%a
)

if defined PID (
    echo  [INFO] Found process PID: %PID%, terminating...
    taskkill /PID %PID% /F >nul 2>&1
    if errorlevel 1 (
        echo  [WARN] Failed to kill process, please close manually.
    ) else (
        echo  [OK] Service stopped successfully.
    )
) else (
    echo  [INFO] No service running on port 8000.
)

echo.
pause
