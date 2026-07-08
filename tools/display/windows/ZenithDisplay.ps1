<#
.SYNOPSIS
    Zenith display autopilot - Windows.

.DESCRIPTION
    Windows counterpart of the Linux zenith-display tool, driving the bundled
    SudoVDA indirect display driver (SudoMaker, MIT/CC0):

      probe    - JSON fingerprint: driver, control interface, monitors
      ensure   - install the bundled driver if missing (needs admin)
      headless - create a virtual display at the client's mode, show only it
      dual     - create a virtual display, extend onto it
      restore  - destroy the virtual display, back to physical layout
      hold     - internal: watchdog pinger spawned by headless/dual

    Control transport is the driver's device interface
    (GUID e5bcc234-1e0c-418a-a0d4-ef8b7501414d) with binary-struct IOCTLs
    from SudoVDA's published sudovda-ioctl.h: ADD_VIRTUAL_DISPLAY carries
    width/height/refresh directly, so the monitor is born at the exact
    Moonlight client mode (SUNSHINE_CLIENT_* env). The driver's watchdog
    auto-removes displays when pings stop, so a hidden holder process pings
    for the session's lifetime — the same pattern as the Linux evdi holder.

.NOTES
    Requires PowerShell 5.1+. 'ensure' requires elevation.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('probe', 'ensure', 'headless', 'dual', 'restore', 'hold')]
    [string]$Command
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- ABI facts (SudoVDA Common/Include/sudovda-ioctl.h) ----------------------

$script:VddInterfaceGuid = [Guid]'e5bcc234-1e0c-418a-a0d4-ef8b7501414d'

function Get-ZenithVddIoctlCode {
    <# CTL_CODE(FILE_DEVICE_UNKNOWN, Function, METHOD_BUFFERED, Access) —
       pure math so tests can pin the wire values. #>
    param([uint32]$Function, [uint32]$Access = 0)  # SudoVDA uses FILE_ANY_ACCESS
    return [uint32]((0x22 -shl 16) -bor ($Access -shl 14) -bor ($Function -shl 2))
}

$script:IoctlAddDisplay = Get-ZenithVddIoctlCode -Function 0x800
$script:IoctlRemoveDisplay = Get-ZenithVddIoctlCode -Function 0x801
$script:IoctlGetWatchdog = Get-ZenithVddIoctlCode -Function 0x803
$script:IoctlDriverPing = Get-ZenithVddIoctlCode -Function 0x888

$script:StateFile = Join-Path $env:LOCALAPPDATA 'Zenith\vdd-state.json'

# Hardware IDs of virtual display drivers we recognize (first = the bundled one).
$script:KnownVddHardwareIds = @(
    'Root\SudoMaker\SudoVDA',   # bundled (SudoVDA)
    'Root\ZakoVDD',             # Sunshine-Foundation lineage
    'Root\MttVDD',              # MikeTheTech Virtual Display Driver
    'Root\Parsec\VDA',          # parsec-vdd
    'Root\IddSampleDriver'      # IddSample derivatives
)

function Test-ZenithVddHardwareId {
    param([string]$HardwareId)
    if (-not $HardwareId) { return $false }
    foreach ($known in $script:KnownVddHardwareIds) {
        if ($HardwareId -like "$known*") { return $true }
    }
    return $false
}

function Get-ZenithClientMode {
    <# Client mode from the env Zenith sets for prep-cmds; sane fallback. #>
    $w = 0; $h = 0; $r = 0
    [void][int]::TryParse($env:SUNSHINE_CLIENT_WIDTH, [ref]$w)
    [void][int]::TryParse($env:SUNSHINE_CLIENT_HEIGHT, [ref]$h)
    [void][int]::TryParse($env:SUNSHINE_CLIENT_FPS, [ref]$r)
    if ($w -lt 1) { $w = 1920 }
    if ($h -lt 1) { $h = 1080 }
    if ($r -lt 1) { $r = 60 }
    [PSCustomObject]@{ Width = [uint32]$w; Height = [uint32]$h; RefreshRate = [uint32]$r }
}

function New-ZenithAddDisplayPayload {
    <# VIRTUAL_DISPLAY_ADD_PARAMS: UINT w,h,refresh + GUID + CHAR[14] name +
       CHAR[14] serial = 56 bytes, packed exactly as the driver reads it. #>
    param(
        [Parameter(Mandatory)][uint32]$Width,
        [Parameter(Mandatory)][uint32]$Height,
        [Parameter(Mandatory)][uint32]$RefreshRate,
        [Parameter(Mandatory)][Guid]$MonitorGuid
    )
    $buf = New-Object byte[] 56
    [BitConverter]::GetBytes($Width).CopyTo($buf, 0)
    [BitConverter]::GetBytes($Height).CopyTo($buf, 4)
    [BitConverter]::GetBytes($RefreshRate).CopyTo($buf, 8)
    $MonitorGuid.ToByteArray().CopyTo($buf, 12)
    [Text.Encoding]::ASCII.GetBytes('ZenithVDA').CopyTo($buf, 28)  # CHAR DeviceName[14]
    [Text.Encoding]::ASCII.GetBytes($MonitorGuid.ToString('N').Substring(0, 13)).CopyTo($buf, 42)  # CHAR SerialNumber[14]
    return , $buf
}

# --- Native interop ----------------------------------------------------------

function Initialize-ZenithVddNative {
    if ('ZenithVddNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class ZenithVddNative {
    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)]
    public static extern int CM_Get_Device_Interface_List_SizeW(
        out uint len, ref Guid classGuid, string deviceId, uint flags);

    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)]
    public static extern int CM_Get_Device_Interface_ListW(
        ref Guid classGuid, string deviceId, char[] buffer, uint bufferLen, uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern SafeFileHandle CreateFileW(
        string fileName, uint access, uint share, IntPtr security,
        uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool DeviceIoControl(
        SafeFileHandle device, uint ioControlCode,
        byte[] inBuffer, uint inSize, byte[] outBuffer, uint outSize,
        out uint bytesReturned, IntPtr overlapped);
}
'@
}

function Get-ZenithVddInterfacePath {
    Initialize-ZenithVddNative
    $guid = $script:VddInterfaceGuid
    $len = [uint32]0
    if ([ZenithVddNative]::CM_Get_Device_Interface_List_SizeW([ref]$len, [ref]$guid, $null, 0) -ne 0 -or $len -le 1) {
        return $null
    }
    $buffer = New-Object char[] $len
    if ([ZenithVddNative]::CM_Get_Device_Interface_ListW([ref]$guid, $null, $buffer, $len, 0) -ne 0) {
        return $null
    }
    $paths = (-join $buffer).Split([char]0) | Where-Object { $_ }
    if ($paths) { return @($paths)[0] }
    return $null
}

function Open-ZenithVddHandle {
    $path = Get-ZenithVddInterfacePath
    if (-not $path) { throw 'VDD control interface not found (driver missing or not started)' }
    # GENERIC_READ|GENERIC_WRITE ([uint32] literal: PS 5.1 parses bare
    # 0xC0000000 as a negative Int32), FILE_SHARE_READ|WRITE, OPEN_EXISTING
    $handle = [ZenithVddNative]::CreateFileW($path, [uint32]'0xC0000000', 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
    if ($handle.IsInvalid) { throw "could not open VDD control interface: $path" }
    return $handle
}

function Invoke-ZenithVddIoctl {
    param(
        [Parameter(Mandatory)]$Handle,
        [Parameter(Mandatory)][uint32]$Code,
        [byte[]]$Payload = $null,
        [uint32]$OutSize = 64
    )
    $out = New-Object byte[] $OutSize
    $returned = [uint32]0
    $inSize = if ($Payload) { [uint32]$Payload.Length } else { [uint32]0 }
    $ok = [ZenithVddNative]::DeviceIoControl($Handle, $Code, $Payload, $inSize, $out, $OutSize, [ref]$returned, [IntPtr]::Zero)
    if (-not $ok) {
        throw "VDD ioctl 0x$($Code.ToString('X')) failed (win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
    }
    return , $out
}

function Test-ZenithVddAlive {
    try {
        $h = Open-ZenithVddHandle
        try { Invoke-ZenithVddIoctl -Handle $h -Code $script:IoctlDriverPing | Out-Null; return $true }
        finally { $h.Close() }
    } catch { return $false }
}

# --- Monitor plumbing --------------------------------------------------------

function Get-ZenithMonitorCount {
    @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction SilentlyContinue).Count
}

function Wait-ZenithMonitorCount {
    param([int]$Above, [int]$TimeoutSec = 15)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ZenithMonitorCount) -gt $Above) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-ZenithVddInterface {
    param([int]$TimeoutSec = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Get-ZenithVddInterfacePath) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Invoke-ZenithTopology {
    param([ValidateSet('/external', '/extend', '/internal')] [string]$Switch)
    & (Join-Path $env:SystemRoot 'System32\DisplaySwitch.exe') $Switch
}

# --- Commands ----------------------------------------------------------------

function Get-ZenithVddDevice {
    Get-PnpDevice -Class Display -ErrorAction SilentlyContinue |
        Where-Object {
            $ids = @($_.HardwareID) + @($_.InstanceId)
            @($ids | Where-Object { Test-ZenithVddHardwareId $_ }).Count -gt 0
        }
}

function Invoke-ZenithProbe {
    $vdd = @(Get-ZenithVddDevice)
    [PSCustomObject]@{
        vdd_driver_present = ($vdd.Count -gt 0)
        vdd_devices        = @($vdd | ForEach-Object { $_.FriendlyName })
        control_interface  = [bool](Get-ZenithVddInterfacePath)
        control_ping       = (Test-ZenithVddAlive)
        monitor_count      = (Get-ZenithMonitorCount)
        is_admin           = ([Security.Principal.WindowsPrincipal] `
                                  [Security.Principal.WindowsIdentity]::GetCurrent()
                             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } | ConvertTo-Json -Depth 4
}

function Invoke-ZenithEnsure {
    if (Get-ZenithVddInterfacePath) {
        Write-Host 'VDD driver present and control interface reachable.'
        return
    }
    $installer = Join-Path $PSScriptRoot 'install-vdd.bat'
    if (-not (Test-Path $installer)) {
        Write-Host "Bundled VDD installer not found next to this script ($installer)."
        Write-Host 'Reinstall Zenith with the Virtual Display component enabled.'
        exit 3
    }
    Write-Host 'Installing the bundled virtual display driver (needs admin consent)...'
    $proc = Start-Process -FilePath $installer -Verb RunAs -Wait -PassThru
    if ($proc.ExitCode -ne 0) { exit $proc.ExitCode }
    if (-not (Wait-ZenithVddInterface)) { exit 3 }
    Write-Host 'VDD driver installed.'
}

function Invoke-ZenithApply {
    param([ValidateSet('headless', 'dual')] [string]$Kind)
    if (-not (Get-ZenithVddInterfacePath)) {
        # Degrade like the Linux tool: a plain-desktop stream beats a dead launch.
        Write-Warning 'no VDD driver available - streaming the physical desktop (run "ZenithDisplay.ps1 ensure")'
        return
    }

    $mode = Get-ZenithClientMode
    $monitorGuid = [Guid]::NewGuid()
    $payload = New-ZenithAddDisplayPayload -Width $mode.Width -Height $mode.Height `
        -RefreshRate $mode.RefreshRate -MonitorGuid $monitorGuid

    $before = Get-ZenithMonitorCount
    $handle = Open-ZenithVddHandle
    try {
        Invoke-ZenithVddIoctl -Handle $handle -Code $script:IoctlAddDisplay -Payload $payload -OutSize 12 | Out-Null
        $watchdog = Invoke-ZenithVddIoctl -Handle $handle -Code $script:IoctlGetWatchdog -OutSize 8
        $timeout = [BitConverter]::ToUInt32($watchdog, 0)
    } finally {
        $handle.Close()
    }

    $stateDir = Split-Path $script:StateFile
    if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
    [PSCustomObject]@{ MonitorGuid = $monitorGuid.ToString(); WatchdogTimeout = $timeout } |
        ConvertTo-Json | Set-Content $script:StateFile

    if ($timeout -gt 0) {
        # The driver removes the display when pings stop; keep a hidden pinger
        # alive for the session (restore deletes the state file to end it).
        Start-Process -WindowStyle Hidden -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$PSCommandPath`"", 'hold'
        ) | Out-Null
    }

    if (-not (Wait-ZenithMonitorCount -Above $before)) {
        Write-Warning 'virtual display did not appear - streaming the physical desktop'
        return
    }
    if ($Kind -eq 'headless') { Invoke-ZenithTopology '/external' }
    else { Invoke-ZenithTopology '/extend' }
}

function Invoke-ZenithHold {
    <# Watchdog pinger: runs hidden until the state file disappears. #>
    if (-not (Test-Path $script:StateFile)) { return }
    $state = Get-Content $script:StateFile -Raw | ConvertFrom-Json
    $interval = [Math]::Max([int]($state.WatchdogTimeout / 3), 1)
    $failures = 0
    while (Test-Path $script:StateFile) {
        if (Test-ZenithVddAlive) { $failures = 0 } else { $failures++ }
        if ($failures -gt 3) { return }
        Start-Sleep -Seconds $interval
    }
}

function Invoke-ZenithRestore {
    if (Test-Path $script:StateFile) {
        try {
            $state = Get-Content $script:StateFile -Raw | ConvertFrom-Json
            $guid = [Guid]$state.MonitorGuid
            $handle = Open-ZenithVddHandle
            try {
                Invoke-ZenithVddIoctl -Handle $handle -Code $script:IoctlRemoveDisplay `
                    -Payload ([byte[]]$guid.ToByteArray()) | Out-Null
            } finally {
                $handle.Close()
            }
        } catch {
            Write-Warning "virtual display removal failed: $($_.Exception.Message)"
        }
        Remove-Item $script:StateFile -Force -ErrorAction SilentlyContinue  # ends the holder
    }
    Invoke-ZenithTopology '/internal'
}

switch ($Command) {
    'probe' { Invoke-ZenithProbe }
    'ensure' { Invoke-ZenithEnsure }
    'headless' { Invoke-ZenithApply 'headless' }
    'dual' { Invoke-ZenithApply 'dual' }
    'restore' { Invoke-ZenithRestore }
    'hold' { Invoke-ZenithHold }
}
