<#
Pester tests for the pure logic in ZenithDisplay.ps1.
Everything touching PnP/WMI/ioctls/DisplaySwitch is exercised only on real machines.
#>
BeforeAll {
    $script:Source = Join-Path $PSScriptRoot 'ZenithDisplay.ps1'
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($script:Source, [ref]$null, [ref]$null)
    $script:Functions = $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
}

Describe 'Test-ZenithVddHardwareId' {
    BeforeAll {
        $script:KnownVddHardwareIds = @(
            'Root\SudoMaker\SudoVDA', 'Root\ZakoVDD', 'Root\MttVDD', 'Root\Parsec\VDA', 'Root\IddSampleDriver'
        )
        $fn = $script:Functions | Where-Object Name -EQ 'Test-ZenithVddHardwareId'
        Invoke-Expression $fn.Extent.Text
    }

    It 'matches the bundled SudoVDA id' {
        Test-ZenithVddHardwareId 'Root\SudoMaker\SudoVDA' | Should -BeTrue
    }
    It 'matches instance-suffixed ids' {
        Test-ZenithVddHardwareId 'Root\SudoMaker\SudoVDA\0001' | Should -BeTrue
    }
    It 'still recognizes foreign VDDs like ZakoVDD' {
        Test-ZenithVddHardwareId 'Root\ZakoVDD' | Should -BeTrue
    }
    It 'rejects real GPUs' {
        Test-ZenithVddHardwareId 'PCI\VEN_10DE&DEV_2860' | Should -BeFalse
    }
    It 'rejects empty input' {
        Test-ZenithVddHardwareId '' | Should -BeFalse
    }
}

Describe 'Get-ZenithVddIoctlCode' {
    BeforeAll {
        $fn = $script:Functions | Where-Object Name -EQ 'Get-ZenithVddIoctlCode'
        Invoke-Expression $fn.Extent.Text
    }

    It 'computes IOCTL_ADD_VIRTUAL_DISPLAY exactly as sudovda-ioctl.h' {
        # CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
        Get-ZenithVddIoctlCode -Function 0x800 | Should -Be 0x222000
    }
    It 'computes IOCTL_REMOVE_VIRTUAL_DISPLAY exactly as sudovda-ioctl.h' {
        Get-ZenithVddIoctlCode -Function 0x801 | Should -Be 0x222004
    }
    It 'computes IOCTL_GET_WATCHDOG exactly as sudovda-ioctl.h' {
        Get-ZenithVddIoctlCode -Function 0x803 | Should -Be 0x22200C
    }
    It 'computes IOCTL_DRIVER_PING exactly as sudovda-ioctl.h' {
        Get-ZenithVddIoctlCode -Function 0x888 | Should -Be 0x222220
    }
}

Describe 'New-ZenithAddDisplayPayload' {
    BeforeAll {
        $fn = $script:Functions | Where-Object Name -EQ 'New-ZenithAddDisplayPayload'
        Invoke-Expression $fn.Extent.Text
        $script:TestGuid = [Guid]'11111111-2222-3333-4444-555555555555'
        $script:Payload = New-ZenithAddDisplayPayload -Width 2420 -Height 1668 -RefreshRate 120 -MonitorGuid $script:TestGuid
    }

    It 'packs VIRTUAL_DISPLAY_ADD_PARAMS to exactly 56 bytes' {
        $script:Payload.Length | Should -Be 56
    }
    It 'packs width/height/refresh little-endian at offsets 0/4/8' {
        [BitConverter]::ToUInt32($script:Payload, 0) | Should -Be 2420
        [BitConverter]::ToUInt32($script:Payload, 4) | Should -Be 1668
        [BitConverter]::ToUInt32($script:Payload, 8) | Should -Be 120
    }
    It 'packs the monitor GUID at offset 12' {
        [Guid][byte[]]$script:Payload[12..27] | Should -Be $script:TestGuid
    }
    It 'packs the ASCII device name at offset 28' {
        [Text.Encoding]::ASCII.GetString($script:Payload, 28, 9) | Should -Be 'ZenithVDA'
        $script:Payload[37] | Should -Be 0  # NUL padding within CHAR[14]
    }
    It 'packs a 13-char serial at offset 42 with NUL terminator' {
        [Text.Encoding]::ASCII.GetString($script:Payload, 42, 13) | Should -Be '1111111122223'
        $script:Payload[55] | Should -Be 0
    }
}

Describe 'Get-ZenithClientMode' {
    BeforeAll {
        $fn = $script:Functions | Where-Object Name -EQ 'Get-ZenithClientMode'
        Invoke-Expression $fn.Extent.Text
    }

    It 'reads the Zenith client env contract' {
        $env:SUNSHINE_CLIENT_WIDTH = '2420'; $env:SUNSHINE_CLIENT_HEIGHT = '1668'; $env:SUNSHINE_CLIENT_FPS = '120'
        $m = Get-ZenithClientMode
        $m.Width | Should -Be 2420
        $m.Height | Should -Be 1668
        $m.RefreshRate | Should -Be 120
    }
    It 'falls back to 1920x1080@60 when unset' {
        $env:SUNSHINE_CLIENT_WIDTH = ''; $env:SUNSHINE_CLIENT_HEIGHT = ''; $env:SUNSHINE_CLIENT_FPS = ''
        $m = Get-ZenithClientMode
        $m.Width | Should -Be 1920
        $m.Height | Should -Be 1080
        $m.RefreshRate | Should -Be 60
    }
}
