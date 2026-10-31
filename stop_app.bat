@echo off
setlocal EnableDelayedExpansion
title RoadsEye Stopper
chcp 65001 >nul

:: ==========================================================================
::  RoadsEye - One-Press Stopper
::  Satu kali klik: matikan backend (8000) + frontend (3000) + node/uvicorn
:: ==========================================================================

echo ==========================================================
echo    ROADSEYE - MEMATIKAN SEMUA LAYANAN
echo ==========================================================
echo.

echo [1/5] Membersihkan port 8000 dan 3000...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [2/5] Menghentikan proses uvicorn dan vite...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn server.app|uvicorn api.main|vite|esbuild' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [3/5] Menghentikan child proses uvicorn --reload...
powershell -NoProfile -Command "$parents = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'multiprocessing.spawn' } | Select-Object -ExpandProperty ProcessId; if ($parents) { Get-CimInstance Win32_Process | Where-Object { $parents -contains $_.ParentProcessId } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; $parents | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }" >nul 2>&1

echo [4/5] Menutup jendela terminal RoadsEye...
powershell -NoProfile -Command "Get-Process cmd,conhost -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'RoadsEye*' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [5/5] Verifikasi port bebas...
powershell -NoProfile -Command "$listening = Get-NetTCPConnection -LocalPort 8000,3000 -State Listen -ErrorAction SilentlyContinue; if ($listening) { $listening | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }" >nul 2>&1

echo.
echo Selesai. Semua layanan RoadsEye telah dimatikan.
timeout /t 2 /nobreak >nul
