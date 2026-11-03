@echo off
chcp 65001 >nul
title 套利盘口查询（关掉这个窗口就停）
cd /d "%~dp0"
"C:\Users\Administrator\AppData\Roaming\uv\python\cpython-3.13.16-windows-x86_64-none\python.exe" 套利盘口查询.py %*
pause
