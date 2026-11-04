param(
    [string]$Python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" })
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "infra/rabbitmq/plat05/compose.yaml"
$projectName = "tvplat05-$([guid]::NewGuid().ToString('N').Substring(0, 10))"
$runtimeRoot = Join-Path $env:TEMP $projectName
$envPath = (Join-Path $runtimeRoot ".env") -replace "\\", "/"
$definitionsPath = Join-Path $runtimeRoot "secrets/definitions.json"
$marker = "plat05-$([guid]::NewGuid().ToString('N'))"
$composeArgs = @("compose", "-p", $projectName, "--env-file", $envPath, "-f", $composeFile)
$definitionsVolume = "$projectName-rabbitmq-definitions"
$seedContainer = "$projectName-definitions-seed"
$definitionsVolumeCreated = $false
$seedContainerCreated = $false
$savedVariables = @{}
foreach ($name in @("PLAT05_ENV_FILE", "PLAT05_TEST_MARKER", "COMPOSE_PROJECT_NAME")) {
    $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

function Invoke-DockerCompose([string[]]$Arguments) {
    & docker @composeArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

try {
    if (-not (Test-Path -LiteralPath $env:TEMP -PathType Container)) {
        throw "Temporary directory is unavailable: $env:TEMP"
    }
    if (Test-Path -LiteralPath $runtimeRoot) {
        throw "Refusing to reuse an existing temporary path: $runtimeRoot"
    }

    & $Python -m unittest scripts.tests.test_rabbitmq_definitions_plat05 -v
    if ($LASTEXITCODE -ne 0) {
        throw "PLAT-05 definition contract tests failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
    $null = & $Python (Join-Path $repoRoot "scripts/gerar_segredos.py") --output-dir $runtimeRoot 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "PLAT-02 secret generation failed with exit code $LASTEXITCODE"
    }

    $env:PLAT05_ENV_FILE = $envPath
    $env:PLAT05_TEST_MARKER = $marker
    $env:COMPOSE_PROJECT_NAME = $projectName

    & docker volume create $definitionsVolume | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not create the isolated RabbitMQ definitions volume" }
    $definitionsVolumeCreated = $true
    Invoke-DockerCompose @("build", "test-client")
    & docker create --name $seedContainer --mount "source=$definitionsVolume,target=/seed" "python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db" sleep 300 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not create the temporary definitions-volume seeder" }
    $seedContainerCreated = $true
    & docker cp $definitionsPath "${seedContainer}:/seed/definitions.json"
    if ($LASTEXITCODE -ne 0) { throw "Could not copy generated definitions into the isolated volume" }
    & docker rm $seedContainer | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not remove the temporary definitions-volume seeder" }
    $seedContainerCreated = $false

    Invoke-DockerCompose @("up", "-d", "--wait", "rabbitmq")
    Invoke-DockerCompose @("run", "--rm", "test-client", "before")
    Invoke-DockerCompose @("restart", "rabbitmq")
    Invoke-DockerCompose @("up", "-d", "--wait", "rabbitmq")
    Invoke-DockerCompose @("run", "--rm", "test-client", "after")
    Write-Output "PASS: PLAT-05 isolated RabbitMQ integration suite completed"
}
finally {
    try {
        if (Test-Path -LiteralPath $definitionsPath) {
            $env:PLAT05_ENV_FILE = $envPath
            $env:PLAT05_TEST_MARKER = $marker
            $env:COMPOSE_PROJECT_NAME = $projectName
            # The unique Compose project name scopes removal to this run's broker, network, and volume.
            & docker @composeArgs down --volumes --remove-orphans
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Compose cleanup failed; inspect only project $projectName before retrying cleanup."
            }
        }
        if ($seedContainerCreated) {
            & docker rm -f $seedContainer | Out-Null
        }
        if ($definitionsVolumeCreated) {
            & docker volume rm $definitionsVolume | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Could not remove the PLAT-05 definitions volume $definitionsVolume; verify it is unused before retrying cleanup."
            }
        }

        if (Test-Path -LiteralPath $runtimeRoot) {
            $resolvedTemp = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
            $resolvedRuntime = [System.IO.Path]::GetFullPath($runtimeRoot)
            if (-not $resolvedRuntime.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase) -or
                [System.IO.Path]::GetFileName($resolvedRuntime) -ne $projectName) {
                throw "Refusing to remove an unexpected temporary path: $resolvedRuntime"
            }
            Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force
        }
    }
    finally {
        foreach ($name in $savedVariables.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedVariables[$name], "Process")
        }
    }
}
