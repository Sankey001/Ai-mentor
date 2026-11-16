@echo off
rem Start AI Mentor. Works whatever the PowerShell execution policy is.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
