@echo off
rem ============================================================
rem  xuexitong - collect diagnostics
rem  Double-click this file to gather a diagnostics bundle.
rem
rem  Kept pure ASCII on purpose: cmd.exe reads .bat in the OEM
rem  codepage, so non-ASCII text here shows up as mojibake on
rem  Chinese Windows. All user-facing Chinese lives in the
rem  PowerShell script, which handles encoding properly.
rem
rem  Does NOT require Xuexitong.exe to be runnable -- "double-click
rem  crashes instantly" is itself a reportable failure, and an exe
rem  based collector could not gather evidence for it.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PS1=%~dp0tools\collect_diagnostics.ps1"
if not exist "%PS1%" set "PS1=%~dp0collect_diagnostics.ps1"

if not exist "%PS1%" (
    echo.
    echo   [ERROR] collect_diagnostics.ps1 not found next to this .bat
    echo           Expected: "%PS1%"
    echo.
    pause
    exit /b 1
)

rem  Pass %CD% (no trailing backslash), not %~dp0: %~dp0 ends with
rem  "\", which PowerShell reads as an escape inside the -File
rem  argument's quotes -- it eats the closing quote and the value
rem  collapses to an empty string.
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Root "%CD%"
set "RC=%ERRORLEVEL%"
popd

echo.
if "%RC%"=="0" (
    echo   Done. Please attach the generated .zip to your issue.
) else (
    echo   [WARN] Collection returned code %RC%. See messages above.
)
echo.
pause
exit /b %RC%