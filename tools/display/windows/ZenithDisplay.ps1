<#
.SYNOPSIS
    Zenith display autopilot - Windows scaffold (EXPERIMENTAL).

.DESCRIPTION
    Windows counterpart of the Linux zenith-display tool. The full
    plug-and-play story on Windows is a bundled signed virtual display
    driver controlled from Zenith itself (Sunshine-Foundation lineage:
    SudoVDA); that native integration is tracked separately. This scaffold
    provides the pieces that are already safe to ship:

      probe    - detect known virtual display drivers and active monitors
      ensure   - bootstrap a VDD driver if none present (downloads the
                 signed MikeTheTech Virtual Display Driver; needs admin)
      headless - show only the virtual display   (DisplaySwitch topology)
      dual     - extend onto the virtual display (DisplaySwitch topology)
      restore  - back to internal-only topology

    Topology switching via DisplaySwitch.exe covers the common
    laptop+VDD case; arbitrary N-monitor topologies need the CCD API and
    land with the native port.

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

# Hardware IDs of virtual display drivers we recognize.
$script:KnownVddHardwareIds = @(
    'Root\SudoMakerVDA',        # SudoVDA (Sunshine-Foundation)
    'Root\MttVDD',              # MikeTheTech Virtual Display Driver
    'Root\Parsec\VDA',          # parsec-vdd
    'Root\IddSampleDriver'      # IddSample derivatives
)

$script:MttVddRelease = 'https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/latest'

function Test-ZenithVddHardwareId {
    <# Pure matcher so tests can cover ID normalization. #>
    param([string]$HardwareId)
    if (-not $HardwareId) { return $false }
    foreach ($known in $script:KnownVddHardwareIds) {
        if ($HardwareId -like "$known*") { return $true }
    }
    return $false
}

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
        monitors           = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction SilentlyContinue |
                                   ForEach-Object { ($_.UserFriendlyName -ne 0 | ForEach-Object { [char]$_ }) -join '' })
        is_admin           = ([Security.Principal.WindowsPrincipal] `
                                  [Security.Principal.WindowsIdentity]::GetCurrent()
                             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } | ConvertTo-Json -Depth 4
}

function Invoke-ZenithEnsure {
    if (Get-ZenithVddDevice) {
        Write-Host 'VDD driver already present.'
        return
    }
    Write-Host "No virtual display driver found. Signed driver: $script:MttVddRelease"
    Write-Host 'Automated install lands with the native Zenith integration;'
    Write-Host 'until then download the release above and run its installer once.'
    exit 3
}

function Invoke-ZenithTopology {
    param([ValidateSet('/external', '/extend', '/internal')] [string]$Switch)
    $displaySwitch = Join-Path $env:SystemRoot 'System32\DisplaySwitch.exe'
    & $displaySwitch $Switch
}

switch ($Command) {
    'probe' { Invoke-ZenithProbe }
    'ensure' { Invoke-ZenithEnsure }
    'headless' { Invoke-ZenithTopology '/external' }
    'dual' { Invoke-ZenithTopology '/extend' }
    'restore' { Invoke-ZenithTopology '/internal' }
}
