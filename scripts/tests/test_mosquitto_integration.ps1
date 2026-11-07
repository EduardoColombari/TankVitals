$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonName = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$python = Get-Command $pythonName -ErrorAction SilentlyContinue
$docker = Get-Command docker -ErrorAction SilentlyContinue
$project = "tankvitals-plat04-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) $project
$testRootCreated = $false
$envFile = Join-Path $testRoot '.env'
$composeFile = Join-Path $repoRoot 'infra\mosquitto\plat04\compose.yaml'
$generator = Join-Path $repoRoot 'scripts\gerar_segredos.py'
. (Join-Path $PSScriptRoot 'NativeProcess.ps1')

if (-not $python) { throw 'Python executable is unavailable.' }
if (-not $docker) { throw 'Docker CLI is unavailable.' }

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$FailureMessage) {
    $result = Invoke-CapturedProcess $Executable $Arguments $repoRoot
    if ($result.ExitCode -ne 0) { throw $FailureMessage }
    return $result
}

if (Test-Path -LiteralPath $testRoot) { throw 'Refusing to reuse an existing temporary path.' }
New-Item -ItemType Directory -Path $testRoot | Out-Null
$testRootCreated = $true
try {
    $null = Invoke-Checked $python.Source @($generator, '--output-dir', $testRoot) 'PLAT-02 could not generate isolated test credentials.'

    $composeArgs = @('compose', '--project-name', $project, '--env-file', $envFile, '--file', $composeFile)
    $null = Invoke-Checked $docker.Source ($composeArgs + @('up', '--detach')) 'Could not start the isolated PLAT-04 broker.'

    $null = Invoke-Checked $docker.Source ($composeArgs + @('exec', '--no-TTY', 'mosquitto', '/bin/sh', '/mosquitto/config/test-acl.sh')) 'Mosquitto ACL integration checks failed.'

    # Verify retained data survives restart and the read-only config/ACL are
    # loaded again. A new client publishes the retained status before restart.
    $null = Invoke-Checked $docker.Source ($composeArgs + @('exec', '--no-TTY', 'mosquitto', '/bin/sh', '-c', 'mosquitto_pub -h 127.0.0.1 -p 1883 -u simulator -P "$MQTT_SIMULATOR_PASSWORD" -t tankvitals/sim/status -r -m plat04-persisted')) 'Could not publish the retained persistence probe.'
    $null = Invoke-Checked $docker.Source ($composeArgs + @('restart', 'mosquitto')) 'Could not restart the isolated broker.'
    $null = Invoke-Checked $docker.Source ($composeArgs + @('exec', '--no-TTY', 'mosquitto', '/bin/sh', '-c', 'mosquitto_sub -h 127.0.0.1 -p 1883 -u gateway -P "$MQTT_GATEWAY_PASSWORD" -t tankvitals/sim/status -C 1 -W 5')) 'Retained status did not survive the broker restart.'
    Write-Output 'PASS: retained status and configuration survived broker restart'
}
finally {
    if ($testRootCreated) {
        $composeArgs = @('compose', '--project-name', $project, '--env-file', $envFile, '--file', $composeFile)
        $null = Invoke-CapturedProcess $docker.Source ($composeArgs + @('down')) $repoRoot

        $resolvedTemp = [System.IO.Path]::GetFullPath($testRoot)
        $expectedTemp = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $project))
        if ($resolvedTemp -eq $expectedTemp -and (Test-Path -LiteralPath $resolvedTemp)) {
            Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
        }
    }
}
