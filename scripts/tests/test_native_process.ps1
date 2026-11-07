$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NativeProcess.ps1')
$node = Get-Command node -ErrorAction SilentlyContinue
$failures = 0

function Assert-Scenario([string]$Name, [bool]$Passed) {
    if ($Passed) {
        Write-Output "PASS: $Name"
    } else {
        Write-Output "FAIL: $Name"
        $script:failures++
    }
}

if (-not $node) {
    Write-Output 'FAIL: Node.js is required for native-process regression tests.'
    exit 1
}

$stderrSuccess = Invoke-CapturedProcess $node.Source @('-e', 'process.stderr.write("npm notice PING\n"); process.stdout.write("ok\n"); process.exit(0)')
Assert-Scenario 'stderr notice does not override exit code 0' ($stderrSuccess.ExitCode -eq 0 -and $stderrSuccess.StdOut -eq "ok`n" -and $stderrSuccess.StdErr -match 'npm notice PING')

$stderrFailure = Invoke-CapturedProcess $node.Source @('-e', 'process.stderr.write("private diagnostic\n"); process.exit(23)')
Assert-Scenario 'nonzero child exit code is preserved with stderr captured separately' ($stderrFailure.ExitCode -eq 23 -and $stderrFailure.StdErr -match 'private diagnostic')

$argument = 'a path with spaces, "quotes", and trailing\'
$encodedExpected = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($argument))
$argumentResult = Invoke-CapturedProcess $node.Source @('-e', 'process.stdout.write(Buffer.from(process.argv[1]).toString("base64"))', $argument)
Assert-Scenario 'quoted native arguments preserve spaces, quotes, and trailing backslash' ($argumentResult.ExitCode -eq 0 -and $argumentResult.StdOut -eq $encodedExpected)

if ($failures -gt 0) { exit 1 }
Write-Output 'PASS: all native-process regression tests passed'
