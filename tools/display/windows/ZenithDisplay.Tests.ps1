<#
Pester tests for the pure logic in ZenithDisplay.ps1.
Everything touching PnP/WMI/ioctls/DisplaySwitch is exercised only on real machines.
#>
BeforeAll {
    $script:Source = Join-Path $PSScriptRoot 'ZenithDisplay.ps1'
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$null, [ref]$null)
    $script:Functions = $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)

    function Import-ZenithFunction {
        param([string]$Name)
        $fn = $script:Functions | Where-Object Name -EQ $Name
        Invoke-Expression $fn.Extent.Text
    }
}

Describe 'Test-ZenithVddHardwareId' {
    BeforeAll {
        $script:KnownVddHardwareIds = @(
            'Root\ZakoVDD', 'Root\SudoMakerVDA', 'Root\MttVDD', 'Root\Parsec\VDA', 'Root\IddSampleDriver'
        )
        Import-ZenithFunction 'Test-ZenithVddHardwareId'
    }

    It 'matches the bundled ZakoVDD id' {
        Test-ZenithVddHardwareId 'Root\ZakoVDD' | Should -BeTrue
    }
    It 'matches SudoVDA ids' {
        Test-ZenithVddHardwareId 'Root\SudoMakerVDA' | Should -BeTrue
    }
    It 'matches instance-suffixed ids' {
        Test-ZenithVddHardwareId 'Root\ZakoVDD\0001' | Should -BeTrue
    }
    It 'rejects real GPUs' {
        Test-ZenithVddHardwareId 'PCI\VEN_10DE&DEV_2860' | Should -BeFalse
    }
    It 'rejects empty input' {
        Test-ZenithVddHardwareId '' | Should -BeFalse
    }
}

Describe 'Get-ZenithVddIoctlCode' {
    BeforeAll { Import-ZenithFunction 'Get-ZenithVddIoctlCode' }

    It 'computes IOCTL_VDD_COMMAND exactly as vdd_control_ioctl.h' {
        # CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_WRITE_DATA)
        Get-ZenithVddIoctlCode -Function 0x800 -Access 2 | Should -Be 0x22A000
    }
    It 'computes IOCTL_VDD_PING exactly as vdd_control_ioctl.h' {
        # CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_READ_ACCESS)
        Get-ZenithVddIoctlCode -Function 0x801 -Access 1 | Should -Be 0x226004
    }
}

Describe 'New-ZenithVddCreateCommand' {
    BeforeAll { Import-ZenithFunction 'New-ZenithVddCreateCommand' }

    It 'is bare CREATEMONITOR without a client guid' {
        New-ZenithVddCreateCommand | Should -Be 'CREATEMONITOR'
    }
    It 'appends the client guid when provided' {
        New-ZenithVddCreateCommand -ClientGuid 'abc-123' | Should -Be 'CREATEMONITOR abc-123'
    }
}
