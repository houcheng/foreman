@echo off
setlocal

set SCRIPT_DIR=%~dp0
set INSTALL_DIR=C:\bin\foreman
set BIN_DIR=C:\bin

if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

copy /Y "%SCRIPT_DIR%foreman-prepare.py" "%INSTALL_DIR%\foreman-prepare.py"
copy /Y "%SCRIPT_DIR%foreman-run.py"     "%INSTALL_DIR%\foreman-run.py"
copy /Y "%SCRIPT_DIR%foreman-report.py"  "%INSTALL_DIR%\foreman-report.py"
copy /Y "%SCRIPT_DIR%FOREMAN.md"         "%INSTALL_DIR%\FOREMAN.md"

echo @python "%INSTALL_DIR%\foreman-prepare.py" %%* > "%BIN_DIR%\foreman-prepare.bat"
echo @python "%INSTALL_DIR%\foreman-run.py"     %%* > "%BIN_DIR%\foreman-run.bat"
echo @python "%INSTALL_DIR%\foreman-report.py"  %%* > "%BIN_DIR%\foreman-report.bat"

echo.
echo Installed to %INSTALL_DIR%
echo Shims created in %BIN_DIR%

echo %PATH% | find /I "C:\bin" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Note: C:\bin is not in your PATH.
    echo Add it via: System Properties -^> Environment Variables -^> Path
)

endlocal
