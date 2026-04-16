@echo off
echo.
echo  ==========================================
echo   eBRAM AI - Starting...
echo  ==========================================
echo.
echo  [INFO] Access at: http://localhost:8000
echo  [INFO] Press Ctrl+C or run stop.bat to stop
echo.

cd /d "%~dp0"

where conda >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] conda not found. Please install Anaconda or Miniconda.
    pause
    exit /b 1
)

call conda activate ebram 2>nul
if errorlevel 1 (
    echo  [INFO] Falling back to conda run...
    conda run -n ebram python app.py
) else (
    python app.py
)

pause
