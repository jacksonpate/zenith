@echo off
rem Adapted from Sunshine-Foundation (AlkaidLab/foundation-sunshine, GPL-3.0):
rem same driver ABI (Root\ZakoVDD device node, ZakoTech registry key), Zenith layout.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
chcp 65001 >nul
setlocal enabledelayedexpansion

if /i "%~1"=="--resolve-only" (
    set "RESOLVE_ONLY=1"
)

rem install
set "DRIVER_ROOT=%~dp0\driver"
set "DRIVER_DIR=%DRIVER_ROOT%\win10"
if not exist "%DRIVER_DIR%\ZakoVDD.inf" (
    set "DRIVER_DIR=%DRIVER_ROOT%\latest"
)
set "CONFIG_SOURCE=%DRIVER_ROOT%\vdd_settings.xml"
set "WIN_BUILD="
set "WIN_BUILD_NUM="
set "WIN_BUILD_SOURCE=registry"

if defined VDD_TEST_WIN_BUILD (
    set "WIN_BUILD=%VDD_TEST_WIN_BUILD%"
    set "WIN_BUILD_SOURCE=override"
) else (
    for /f "tokens=3" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CurrentBuildNumber 2^>nul ^| find /i "CurrentBuildNumber"') do set "WIN_BUILD=%%A"
    if not defined WIN_BUILD (
        for /f "tokens=3" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CurrentBuild 2^>nul ^| find /i "CurrentBuild"') do set "WIN_BUILD=%%A"
    )
)

if defined WIN_BUILD (
    echo(!WIN_BUILD!| findstr /r "^[0-9][0-9]*$" >nul
    if not errorlevel 1 (
        set "WIN_BUILD_NUM=!WIN_BUILD!"
    )
)

if not defined WIN_BUILD (
    echo WARNING: Could not detect Windows build; defaulting to Win10 payload.
)

if defined WIN_BUILD if not defined WIN_BUILD_NUM (
    echo WARNING: Ignoring non-numeric Windows build "!WIN_BUILD!" from !WIN_BUILD_SOURCE!; defaulting to Win10 payload.
)

if defined WIN_BUILD_NUM if !WIN_BUILD_NUM! GEQ 22000 if exist "%DRIVER_ROOT%\latest\ZakoVDD.inf" (
    set "DRIVER_DIR=%DRIVER_ROOT%\latest"
)

if not exist "%DRIVER_DIR%\ZakoVDD.inf" (
    set "DRIVER_DIR=%DRIVER_ROOT%"
)

if exist "%DRIVER_DIR%\vdd_settings.xml" (
    set "CONFIG_SOURCE=%DRIVER_DIR%\vdd_settings.xml"
)

if not exist "%DRIVER_DIR%\ZakoVDD.inf" (
    echo ERROR: VDD driver payload not found in "%DRIVER_DIR%"
    exit /b 1
)

if defined WIN_BUILD_NUM (
    echo Detected Windows build: !WIN_BUILD_NUM!
)
if not defined WIN_BUILD_NUM if defined WIN_BUILD echo Detected Windows build (raw): !WIN_BUILD!
echo Using VDD payload: !DRIVER_DIR!

if defined RESOLVE_ONLY goto :resolve_only

rem Get sunshine root directory
for %%I in ("%~dp0\..") do set "ROOT_DIR=%%~fI"

set "DIST_DIR=%ROOT_DIR%\tools\vdd"
set "CONFIG_DIR=%ROOT_DIR%\config"
set "NEFCON=%ROOT_DIR%\tools\nefconw.exe"
if not exist "%NEFCON%" set "NEFCON=%DIST_DIR%\nefconw.exe"
set "VDD_CONFIG=%CONFIG_DIR%\vdd_settings.xml"

rem First, copy files to target directory so nefconw.exe can be used
if exist "%DIST_DIR%" (
    rmdir /s /q "%DIST_DIR%"
)
mkdir "%DIST_DIR%"
copy /y "%DRIVER_DIR%\*.*" "%DIST_DIR%" >nul

rem Now we can use nefconw.exe to thoroughly clean up existing VDD adapters
echo Thoroughly cleaning up existing VDD adapters...

rem Remove all device nodes with the same hardware ID (multiple instances)
echo Removing all existing device nodes...
"%NEFCON%" --remove-device-node --hardware-id Root\ZakoVDD --class-guid 4d36e968-e325-11ce-bfc1-08002be10318
if %ERRORLEVEL% EQU 0 (
    echo Successfully removed device node
) else (
    echo Device node removal failed or not found
)

rem Wait to ensure device is completely removed
timeout /t 3 /nobreak 1>nul

rem Try to uninstall driver completely
echo Uninstalling VDD driver...
"%NEFCON%" --uninstall-driver --inf-path "%DIST_DIR%\ZakoVDD.inf"
if %ERRORLEVEL% EQU 0 (
    echo Successfully uninstalled driver
) else (
    echo Driver uninstall failed or not found
)

rem Wait to ensure driver is completely uninstalled
timeout /t 3 /nobreak 1>nul

rem Clean up registry entries
echo Cleaning registry...
reg delete "HKLM\SOFTWARE\ZakoTech\ZakoDisplayAdapter" /f 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Successfully cleaned registry
) else (
    echo Registry cleanup failed or not found
)

rem Additional cleanup - remove any remaining device instances
echo Performing additional cleanup...
"%NEFCON%" --remove-device-node --hardware-id Root\ZakoVDD --class-guid 4d36e968-e325-11ce-bfc1-08002be10318 2>nul
timeout /t 2 /nobreak 1>nul

rem Wait a bit more to ensure everything is cleaned up
timeout /t 5 /nobreak 1>nul

if not exist "%VDD_CONFIG%" (
    copy /y "%CONFIG_SOURCE%" "%VDD_CONFIG%" >nul
)

@REM write registry
reg add "HKLM\SOFTWARE\ZakoTech\ZakoDisplayAdapter" /v VDDPATH /t REG_SZ /d "%CONFIG_DIR%" /f

@REM rem install cet
set "CERTIFICATE=%DIST_DIR%\ZakoVDD.cer"
certutil -addstore -f root "%CERTIFICATE%"
@REM certutil -addstore -f TrustedPublisher %CERTIFICATE%

@REM install inf
echo Installing VDD adapter...
"%NEFCON%" --create-device-node --hardware-id Root\ZakoVDD --service-name ZAKO_HDR_FOR_SUNSHINE --class-name Display --class-guid 4D36E968-E325-11CE-BFC1-08002BE10318
"%NEFCON%" --install-driver --inf-path "%DIST_DIR%\ZakoVDD.inf"

echo VDD installation completed!
goto :eof

:resolve_only
echo RESOLVED_WIN_BUILD=!WIN_BUILD!
echo RESOLVED_WIN_BUILD_NUM=!WIN_BUILD_NUM!
echo RESOLVED_DRIVER_DIR=!DRIVER_DIR!
echo RESOLVED_CONFIG_SOURCE=!CONFIG_SOURCE!
exit /b 0