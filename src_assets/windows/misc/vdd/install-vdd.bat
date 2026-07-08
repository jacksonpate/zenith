@echo off
rem Install the bundled SudoVDA virtual display driver (SudoMaker, MIT/CC0).
rem UMDF driver, self-signed: trusting the certificate in Root and
rem TrustedPublisher is sufficient on stock Windows — no test mode, Secure
rem Boot stays on. Same flow the Apollo project ships at scale.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
chcp 65001 >nul
setlocal

rem Zenith install root (this script lives in scripts\)
for %%I in ("%~dp0\..") do set "ROOT_DIR=%%~fI"

set "DRIVER_SRC=%~dp0driver"
set "DIST_DIR=%ROOT_DIR%\tools\vdd"
set "NEFCON=%ROOT_DIR%\tools\nefconw.exe"
if not exist "%NEFCON%" set "NEFCON=%DIST_DIR%\nefconw.exe"

if not exist "%DRIVER_SRC%\SudoVDA.inf" (
    echo ERROR: SudoVDA driver payload not found in "%DRIVER_SRC%"
    exit /b 1
)

rem Stage the payload where the uninstaller can always find it
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
mkdir "%DIST_DIR%"
copy /y "%DRIVER_SRC%\*.*" "%DIST_DIR%" >nul

echo Trusting the SudoVDA driver certificate...
certutil -addstore -f root "%DIST_DIR%\sudovda.cer"
certutil -addstore -f TrustedPublisher "%DIST_DIR%\sudovda.cer"

rem Migrate: remove any ZakoVDD device from earlier Zenith builds
"%NEFCON%" --remove-device-node --hardware-id Root\ZakoVDD --class-guid 4d36e968-e325-11ce-bfc1-08002be10318 >nul 2>&1

echo Removing any existing SudoVDA device node...
"%NEFCON%" --remove-device-node --hardware-id root\sudomaker\sudovda --class-guid 4d36e968-e325-11ce-bfc1-08002be10318

echo Installing the SudoVDA adapter...
"%NEFCON%" --create-device-node --class-name Display --class-guid 4D36E968-E325-11CE-BFC1-08002BE10318 --hardware-id root\sudomaker\sudovda
"%NEFCON%" --install-driver --inf-path "%DIST_DIR%\SudoVDA.inf"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: driver installation failed.
    exit /b 1
)

echo VDD installation completed.
