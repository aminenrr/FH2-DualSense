@echo off
cd /d "%~dp0"
python fh2_ps_install.py uninstall %*
pause
