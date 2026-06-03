@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_single_process.ps1" %*
