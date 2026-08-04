@echo off
REM ===========================================================================
REM Protein Ledger -- double-click installer for Windows (GFP-161).
REM
REM WHY THIS FILE EXISTS. PowerShell refuses to run a DOWNLOADED .ps1 under the
REM default RemoteSigned policy: the file carries a mark-of-the-web tag and is
REM unsigned, so the user is told it "cannot be loaded" and left there.
REM
REM Our previous answer was to document
REM     powershell -ExecutionPolicy Bypass -File .\install.ps1
REM which asks somebody who wants to plan groceries to type a command that
REM disables a security control. That is a bad thing to teach anyone.
REM
REM A .cmd file carries NO execution-policy restriction at all, so this runs on
REM a double-click and applies the bypass to ONE invocation of PowerShell --
REM the user's machine-wide policy is never read, changed, or mentioned.
REM
REM   %~dp0   the folder THIS file is in, with a trailing backslash, so the
REM           installer is found no matter what the working directory is
REM           (double-clicking from Explorer often starts in C:\Windows).
REM   %*      forwards -DryRun / -NoIntegrate / -Prefix straight through.
REM
REM KNOWN LIMIT: -ExecutionPolicy Bypass is overridden by GROUP POLICY. On a
REM corporate-managed machine with GPO-enforced AllSigned this will not help,
REM and only an Authenticode-signed script will. A home machine has no GPO.
REM ===========================================================================
setlocal
echo.
echo   Installing Protein Ledger...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RESULT=%ERRORLEVEL%
echo.
if %RESULT% NEQ 0 (
    echo   The installer reported a problem ^(exit code %RESULT%^).
    echo   The messages above say what went wrong.
) else (
    echo   Done. Open Protein Ledger from the Start Menu.
)
echo.
REM Without this the window closes instantly on a double-click and the user
REM sees nothing at all -- success or failure.
pause
endlocal
exit /b %RESULT%
