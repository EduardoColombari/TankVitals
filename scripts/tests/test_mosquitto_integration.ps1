$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$project = "tankvitals-plat04-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) $project
$envFile = Join-Path $testRoot '.env'
$composeFile = Join-Path $repoRoot 'infra\mosquitto\plat04\compose.yaml'
$generator = Join-Path $repoRoot 'scripts\gerar_segredos.py'

New-Item -ItemType Directory -Path $testRoot | Out-Null
Push-Location $repoRoot
try {
    & $python $generator --output-dir $testRoot *> $null
    if ($LASTEXITCODE -ne 0) { throw 'PLAT-02 could not generate isolated test credentials.' }

    & docker compose --project-name $project --env-file $envFile --file $composeFile up --detach
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the isolated PLAT-04 broker.' }

    & docker compose --project-name $project --env-file $envFile --file $composeFile exec --no-TTY mosquitto /bin/sh /mosquitto/config/test-acl.sh
    if ($LASTEXITCODE -ne 0) { throw 'Mosquitto ACL integration checks failed.' }

    # Verify retained data survives restart and the read-only config/ACL are
    # loaded again. A new client publishes the retained status before restart.
    & docker compose --project-name $project --env-file $envFile --file $composeFile exec --no-TTY mosquitto /bin/sh -c 'mosquitto_pub -h 127.0.0.1 -p 1883 -u simulator -P "$MQTT_SIMULATOR_PASSWORD" -t tankvitals/sim/status -r -m plat04-persisted'
    if ($LASTEXITCODE -ne 0) { throw 'Could not publish the retained persistence probe.' }
    & docker compose --project-name $project --env-file $envFile --file $composeFile restart mosquitto
    if ($LASTEXITCODE -ne 0) { throw 'Could not restart the isolated broker.' }
    & docker compose --project-name $project --env-file $envFile --file $composeFile exec --no-TTY mosquitto /bin/sh -c 'mosquitto_sub -h 127.0.0.1 -p 1883 -u gateway -P "$MQTT_GATEWAY_PASSWORD" -t tankvitals/sim/status -C 1 -W 5'
    if ($LASTEXITCODE -ne 0) { throw 'Retained status did not survive the broker restart.' }
    Write-Output 'PASS: retained status and configuration survived broker restart'
}
finally {
    & docker compose --project-name $project --env-file $envFile --file $composeFile down
    Pop-Location

    $resolvedTemp = [System.IO.Path]::GetFullPath($testRoot)
    $expectedTemp = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $project))
    if ($resolvedTemp -eq $expectedTemp -and (Test-Path -LiteralPath $resolvedTemp)) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
