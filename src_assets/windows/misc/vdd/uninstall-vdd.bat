@echo off
rem Remove the bundled SudoVDA virtual display driver.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
chcp 65001 >nul

for %%I in ("%~dp0\..") do set "ROOT_DIR=%%~fI"

set "DIST_DIR=%ROOT_DIR%\tools\vdd"
set "NEFCON=%ROOT_DIR%\tools\nefconw.exe"
if not exist "%NEFCON%" set "NEFCON=%DIST_DIR%\nefconw.exe"
if not exist "%NEFCON%" (
    echo WARNING: nefconw.exe not found, skipping driver/device removal.
    goto :cleanup
)

echo Removing SudoVDA device node...
"%NEFCON%" --remove-device-node --hardware-id root\sudomaker\sudovda --class-guid 4d36e968-e325-11ce-bfc1-08002be10318

if exist "%DIST_DIR%\SudoVDA.inf" (
    echo Uninstalling SudoVDA driver package...
    "%NEFCON%" --uninstall-driver --inf-path "%DIST_DIR%\SudoVDA.inf"
)

:cleanup
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
echo VDD uninstall completed.
