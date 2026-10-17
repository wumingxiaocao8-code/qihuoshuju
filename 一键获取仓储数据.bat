@echo off
chcp 65001 >nul
cd /d "%~dp0"
python futures_brief.py stock --open
if errorlevel 1 (pause) else (timeout /t 5 >nul)
