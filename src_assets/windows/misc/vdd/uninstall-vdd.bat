@echo off
rem Adapted from Sunshine-Foundation (AlkaidLab/foundation-sunshine, GPL-3.0):
rem same driver ABI (Root\ZakoVDD device node, ZakoTech registry key), Zenith layout.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
chcp 65001 >nul

rem Get sunshine root directory
for %%I in ("%~dp0\..") do set "ROOT_DIR=%%~fI"

rem uninstall
set "DIST_DIR=%ROOT_DIR%\tools\vdd"
set "NEFCON=%ROOT_DIR%\tools\nefconw.exe"
if not exist "%NEFCON%" set "NEFCON=%DIST_DIR%\nefconw.exe"
if not exist "%NEFCON%" (
    echo WARNING: nefconw.exe not found, skipping driver/device removal.
    goto :cleanup
)

rem 1) Remove device node(s) first so the driver is no longer in use
echo Removing VDD device node...
"%NEFCON%" --remove-device-node --hardware-id Root\ZakoVDD --class-guid 4d36e968-e325-11ce-bfc1-08002be10318

rem Brief wait so the kernel finishes releasing the device handle before we
rem attempt to remove the driver package from the DriverStore.
timeout /t 1 /nobreak >nul 2>&1

rem 2) Uninstall the driver package from the DriverStore (requires INF path)
if exist "%DIST_DIR%\ZakoVDD.inf" (
    echo Uninstalling VDD driver package...
    "%NEFCON%" --uninstall-driver --inf-path "%DIST_DIR%\ZakoVDD.inf"
) else (
    echo WARNING: ZakoVDD.inf not found in "%DIST_DIR%", skipping driver package uninstall.
)

rem 3) Best-effort second pass in case multiple device instances remain
"%NEFCON%" --remove-device-node --hardware-id Root\ZakoVDD --class-guid 4d36e968-e325-11ce-bfc1-08002be10318 >nul 2>&1

:cleanup
echo Cleaning registry...
reg delete "HKLM\SOFTWARE\ZakoTech" /f 2>nul
if exist "%DIST_DIR%" (
    rmdir /S /Q "%DIST_DIR%"
)
echo VDD uninstall completed.
