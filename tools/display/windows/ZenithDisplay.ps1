<#
.SYNOPSIS
    Zenith display autopilot - Windows.

.DESCRIPTION
    Windows counterpart of the Linux zenith-display tool, driving the bundled
    ZakoVDD indirect display driver (Sunshine-Foundation lineage, GPL-3.0):

      probe    - JSON fingerprint: driver, control interface, monitors
      ensure   - install the bundled driver if missing (needs admin)
      headless - create the virtual display, show only it
      dual     - create the virtual display, extend onto it
      restore  - destroy the virtual display, back to physical layout

    Control transport is the driver's WDF device interface
    (GUID DA9F8C2B-7E4F-49A1-9D4E-6F2B0E1A0C4D) carrying NUL-terminated
    UTF-16 commands (CREATEMONITOR / DESTROYMONITOR) via IOCTL 0x800; the
    contract is Foundation's vdd_control_ioctl.h, which both repos keep
    byte-identical. Resolution/refresh matching to the Moonlight client is
    Zenith's own display-device layer (dd_* options); topology here uses
    DisplaySwitch, with exact CCD control landing with the native port.

.NOTES
    Requires PowerShell 5.1+. 'ensure' requires elevation.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('probe', 'ensure', 'headless', 'dual', 'restore')]
    [string]$Command
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- ABI facts (Foundation vdd_control_ioctl.h) -----------------------------

$script:VddInterfaceGuid = [Guid]'DA9F8C2B-7E4F-49A1-9D4E-6F2B0E1A0C4D'

function Get-ZenithVddIoctlCode {
    <# CTL_CODE(FILE_DEVICE_UNKNOWN, Function, METHOD_BUFFERED, Access) —
       pure math so tests can pin the wire values. #>
    param([uint32]$Function, [uint32]$Access)
    return [uint32]((0x22 -shl 16) -bor ($Access -shl 14) -bor ($Function -shl 2))
}

$script:IoctlVddCommand = Get-ZenithVddIoctlCode -Function 0x800 -Access 2  # FILE_WRITE_DATA
$script:IoctlVddPing = Get-ZenithVddIoctlCode -Function 0x801 -Access 1     # FILE_READ_ACCESS

# Hardware IDs of virtual display drivers we recognize (first = the bundled one).
$script:KnownVddHardwareIds = @(
    'Root\ZakoVDD',             # bundled (Sunshine-Foundation lineage)
    'Root\SudoMakerVDA',        # SudoVDA
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

function New-ZenithVddCreateCommand {
    <# CREATEMONITOR grammar: bare, or with a stable per-client GUID so the
       driver reuses monitor identity (EDID serial) across sessions. #>
    param([string]$ClientGuid)
    if ($ClientGuid) { return "CREATEMONITOR $ClientGuid" }
    return 'CREATEMONITOR'
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

function Send-ZenithVddIoctl {
    param(
        [Parameter(Mandatory)][uint32]$Code,
        [byte[]]$Payload = $null
    )
    $path = Get-ZenithVddInterfacePath
    if (-not $path) { throw 'VDD control interface not found (driver missing or not started)' }
    # GENERIC_READ|GENERIC_WRITE, FILE_SHARE_READ|WRITE, OPEN_EXISTING
    $handle = [ZenithVddNative]::CreateFileW($path, 0xC0000000, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
    if ($handle.IsInvalid) { throw "could not open VDD control interface: $path" }
    try {
        $out = New-Object byte[] 4096
        $returned = [uint32]0
        $inSize = if ($Payload) { [uint32]$Payload.Length } else { [uint32]0 }
        $ok = [ZenithVddNative]::DeviceIoControl($handle, $Code, $Payload, $inSize, $out, 4096, [ref]$returned, [IntPtr]::Zero)
        if (-not $ok) {
            throw "VDD ioctl 0x$($Code.ToString('X')) failed (win32=$([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
        }
        if ($returned -gt 0) { return [Text.Encoding]::Unicode.GetString($out, 0, [int]$returned).TrimEnd([char]0) }
        return ''
    } finally {
        $handle.Close()
    }
}

function Send-ZenithVddCommand {
    param([Parameter(Mandatory)][string]$Text)
    $bytes = [Text.Encoding]::Unicode.GetBytes($Text + [char]0)
    return Send-ZenithVddIoctl -Code $script:IoctlVddCommand -Payload $bytes
}

function Test-ZenithVddAlive {
    try { Send-ZenithVddIoctl -Code $script:IoctlVddPing | Out-Null; return $true }
    catch { return $false }
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
            ($ids | Where-Object { Test-ZenithVddHardwareId $_ }).Count -gt 0
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
    $before = Get-ZenithMonitorCount
    Send-ZenithVddCommand (New-ZenithVddCreateCommand -ClientGuid $env:SUNSHINE_CLIENT_UUID) | Out-Null
    if (-not (Wait-ZenithMonitorCount -Above $before)) {
        Write-Warning 'virtual display did not appear - streaming the physical desktop'
        return
    }
    if ($Kind -eq 'headless') { Invoke-ZenithTopology '/external' }
    else { Invoke-ZenithTopology '/extend' }
}

function Invoke-ZenithRestore {
    if (Get-ZenithVddInterfacePath) {
        try { Send-ZenithVddCommand 'DESTROYMONITOR' | Out-Null }
        catch { Write-Warning "DESTROYMONITOR failed: $($_.Exception.Message)" }
    }
    Invoke-ZenithTopology '/internal'
}

switch ($Command) {
    'probe' { Invoke-ZenithProbe }
    'ensure' { Invoke-ZenithEnsure }
    'headless' { Invoke-ZenithApply 'headless' }
    'dual' { Invoke-ZenithApply 'dual' }
    'restore' { Invoke-ZenithRestore }
}
