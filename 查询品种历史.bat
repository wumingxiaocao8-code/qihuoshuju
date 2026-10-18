@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 查询某个品种过去的仓单日报和库存周报
echo.
set CODE=
set DAYS=
set /p CODE=请输入品种代码或名字（如 al、cu、铝；多个用空格隔开）：
set /p DAYS=往前看多少天（直接回车 = 365）：
if "%DAYS%"=="" set DAYS=365
python futures_brief.py history %CODE% --days %DAYS% --open
if errorlevel 1 (pause) else (timeout /t 5 >nul)
