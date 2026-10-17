@echo off
setlocal EnableDelayedExpansion
title RoadsEye Launcher
chcp 65001 >nul

:: ==========================================================================
::  RoadsEye - One-Press Launcher
::  Satu kali klik: setup (jika perlu) + jalankan backend + frontend + browser
:: ==========================================================================

:: Pastikan working directory adalah folder tempat file .bat ini berada
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
cd /d "%PROJECT_DIR%"

echo ==========================================================
echo    ROADSEYE - CCTV TRAFFIC ANALYTICS
echo           Sistem Analisis Volume Lalu Lintas
echo ==========================================================
echo.
echo Direktori Proyek: %PROJECT_DIR%
echo.

:: -- 1. Deteksi Python -----------------------------------------------------
set "PYTHON_EXE="
if exist "%PROJECT_DIR%\venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_DIR%\venv\Scripts\python.exe"
    echo [OK] Virtual environment terdeteksi.
) else (
    where py >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=py"
        echo [WARN] venv belum ada, pakai launcher "py" sistem.
    ) else (
        where python >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=python"
            echo [WARN] venv belum ada, pakai python sistem.
        ) else (
            echo [ERROR] Python tidak ditemukan. Install Python 3.10+ dan coba lagi.
            pause
            exit /b 1
        )
    )
)

:: -- 2. Setup venv + dependencies (hanya saat pertama kali / auto-heal) ----
set "NEED_INSTALL=0"
if not exist "%PROJECT_DIR%\venv\Scripts\python.exe" set "NEED_INSTALL=1"
if "!PYTHON_EXE!"=="py" set "PYTHON_EXE=py -3"

if "!NEED_INSTALL!"=="1" (
    echo [1/5] Membuat virtual environment - sekali saja...
    !PYTHON_EXE! -m venv "%PROJECT_DIR%\venv"
    set "VENV_PY=%PROJECT_DIR%\venv\Scripts\python.exe"
    echo       Menginstall dependencies Python - butuh internet, beberapa menit...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r "%PROJECT_DIR%\requirements.txt"
    if !errorlevel! neq 0 (
        echo [ERROR] Gagal install dependencies Python.
        pause
        exit /b 1
    )
) else (
    :: Auto-heal: verifikasi dependensi inti, install ulang bila korup
    "%PYTHON_EXE%" -c "import fastapi, uvicorn, cv2, numpy, aiosqlite" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [1/5] Dependencies tidak lengkap, memperbaiki otomatis...
        "%PYTHON_EXE%" -m pip install -r "%PROJECT_DIR%\requirements.txt"
        if !errorlevel! neq 0 (
            echo [ERROR] Gagal install dependencies Python.
            pause
            exit /b 1
        )
    ) else (
        echo [1/5] Dependencies Python siap.
    )
)

:: -- 3. Setup frontend (hanya saat pertama kali) ---------------------------
where npm >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Node.js / npm tidak ditemukan. Install Node.js 20+ lalu coba lagi.
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\dashboard\node_modules" (
    echo [2/5] Menginstall dependencies frontend - sekali saja...
    cd /d "%PROJECT_DIR%\dashboard"
    call npm install --no-audit --no-fund
    if !errorlevel! neq 0 (
        echo [ERROR] Gagal install dependencies Node.
        pause
        exit /b 1
    )
    cd /d "%PROJECT_DIR%"
) else (
    echo [2/5] Dependencies frontend siap.
)

:: -- 4. Bersihkan port 8000 dan 3000 dari sesi lama ------------------------
echo [3/5] Membersihkan port jaringan...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

:: -- 5. Jalankan backend + frontend di jendela terpisah --------------------
echo [4/5] Menjalankan Backend API RoadsEye - Port 8000...
start "RoadsEye - Backend" /d "%PROJECT_DIR%" cmd /k "title RoadsEye - Backend (Port 8000) & "%PROJECT_DIR%\venv\Scripts\python.exe" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload"

echo       Menjalankan Frontend Dashboard RoadsEye - Port 3000...
start "RoadsEye - Frontend" /d "%PROJECT_DIR%\dashboard" cmd /k "title RoadsEye - Frontend (Port 3000) & npm run dev"

:: -- 6. Tunggu server siap lalu buka browser -------------------------------
echo [5/5] Menunggu server siap...
set "READY=0"
for /L %%i in (1,1,30) do (
    if "!READY!"=="0" (
        timeout /t 2 /nobreak >nul
        curl -s -o nul http://localhost:3000
        if !errorlevel! equ 0 set "READY=1"
    )
)

echo.
echo ==========================================================
echo  RoadsEye berhasil dijalankan.
echo  - Backend API : http://localhost:8000
echo  - Frontend UI : http://localhost:3000
echo  - API Docs    : http://localhost:8000/docs
echo.
echo  * Jangan tutup kedua jendela terminal - Backend dan Frontend.
echo  * Untuk mematikan semua layanan, jalankan stop_app.bat.
echo ==========================================================
echo.

start http://localhost:3000
echo Dashboard sudah terbuka di browser. Tekan tombol apa saja untuk menutup jendela ini.
pause >nul
