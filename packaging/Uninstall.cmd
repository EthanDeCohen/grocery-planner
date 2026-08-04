@echo off
REM Protein Ledger -- double-click uninstaller (GFP-161).
REM Same reasoning as Install.cmd: a .cmd has no execution-policy restriction,
REM so removing the app never requires typing a bypass command either.
setlocal
echo.
echo   Uninstalling Protein Ledger...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
set RESULT=%ERRORLEVEL%
echo.
pause
endlocal
exit /b %RESULT%
