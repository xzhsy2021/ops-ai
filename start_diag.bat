@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\preflight_start_check.ps1" %*
