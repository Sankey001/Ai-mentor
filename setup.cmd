@echo off
rem Install or update AI Mentor (options: see README). Works whatever the PowerShell execution policy is.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
