@echo off
cd /d "%~dp0app"
net session >nul 2>&1 || (powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'" & exit /b)
if not exist "%~dp0FH2 DualSense.lnk" powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%~dp0FH2 DualSense.lnk');$s.TargetPath='%~f0';$s.IconLocation='%~dp0app\icon.ico';$s.WorkingDirectory='%~dp0';$s.WindowStyle=7;$s.Save()"
if not exist ".deps_ok" (
  echo First run: installing Python packages...
  python -m pip install --quiet hidapi sounddevice numpy scipy && echo ok> ".deps_ok"
)
where pythonw >nul 2>&1 && (start "" pythonw fh2_dualsense.pyw) || (start "" python fh2_dualsense.pyw)
