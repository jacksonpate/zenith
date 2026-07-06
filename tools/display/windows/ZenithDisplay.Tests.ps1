<#
Pester tests for the pure logic in ZenithDisplay.ps1.
Everything touching PnP/WMI/DisplaySwitch is exercised only on real machines.
#>
BeforeAll {
    # Dot-source with a harmless command; probe path isn't invoked in tests.
    $script:Source = Join-Path $PSScriptRoot 'ZenithDisplay.ps1'
}

Describe 'Test-ZenithVddHardwareId' {
    BeforeAll {
        # Extract just the matcher function without executing the script body.
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$null, [ref]$null)
        $fn = $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
            Where-Object Name -EQ 'Test-ZenithVddHardwareId'
        $script:KnownVddHardwareIds = @(
            'Root\SudoMakerVDA', 'Root\MttVDD', 'Root\Parsec\VDA', 'Root\IddSampleDriver'
        )
        Invoke-Expression $fn.Extent.Text
    }

    It 'matches SudoVDA ids' {
        Test-ZenithVddHardwareId 'Root\SudoMakerVDA' | Should -BeTrue
    }
    It 'matches instance-suffixed ids' {
        Test-ZenithVddHardwareId 'Root\MttVDD\0001' | Should -BeTrue
    }
    It 'rejects real GPUs' {
        Test-ZenithVddHardwareId 'PCI\VEN_10DE&DEV_2860' | Should -BeFalse
    }
    It 'rejects empty input' {
        Test-ZenithVddHardwareId '' | Should -BeFalse
    }
}
