@echo off
chcp 65001 >nul
cd /d "%~dp0"
set FAIL=0
echo ===== 1/2 影响因素（新闻 + 价差）=====
python futures_brief.py factors
if errorlevel 1 set FAIL=1
echo.
echo ===== 2/2 仓储数据（仓单 + 库存）=====
python futures_brief.py stock --open
if errorlevel 1 set FAIL=1
if "%FAIL%"=="1" (pause) else (timeout /t 5 >nul)
