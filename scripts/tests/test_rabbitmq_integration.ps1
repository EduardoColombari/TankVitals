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
$docker = Get-Command docker -ErrorAction SilentlyContinue
$python = Get-Command $Python -ErrorAction SilentlyContinue
$definitionsVolumeCreated = $false
$seedContainerCreated = $false
$runtimeCreated = $false
$savedVariables = @{}
foreach ($name in @("PLAT05_ENV_FILE", "PLAT05_TEST_MARKER", "COMPOSE_PROJECT_NAME")) {
    $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
. (Join-Path $PSScriptRoot "NativeProcess.ps1")

function Invoke-DockerCompose([string[]]$Arguments) {
    $result = Invoke-CapturedProcess $script:docker.Source ($script:composeArgs + $Arguments) $script:repoRoot
    if ($result.ExitCode -ne 0) {
        throw "docker compose failed; diagnostics omitted."
    }
}

try {
    if (-not (Test-Path -LiteralPath $env:TEMP -PathType Container)) {
        throw "Temporary directory is unavailable: $env:TEMP"
    }
    if (Test-Path -LiteralPath $runtimeRoot) {
        throw "Refusing to reuse an existing temporary path: $runtimeRoot"
    }

    if (-not $docker) { throw "Docker CLI is unavailable." }
    if (-not $python) { throw "Python executable is unavailable." }

    $unitTests = Invoke-CapturedProcess $python.Source @("-m", "unittest", "scripts.tests.test_rabbitmq_definitions_plat05", "-v") $repoRoot
    if ($unitTests.ExitCode -ne 0) {
        throw "PLAT-05 definition contract tests failed; diagnostics omitted."
    }

    New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
    $runtimeCreated = $true
    $generation = Invoke-CapturedProcess $python.Source @((Join-Path $repoRoot "scripts/gerar_segredos.py"), "--output-dir", $runtimeRoot) $repoRoot
    if ($generation.ExitCode -ne 0) {
        throw "PLAT-02 secret generation failed; diagnostics omitted."
    }

    $env:PLAT05_ENV_FILE = $envPath
    $env:PLAT05_TEST_MARKER = $marker
    $env:COMPOSE_PROJECT_NAME = $projectName

    $volume = Invoke-CapturedProcess $docker.Source @("volume", "create", $definitionsVolume) $repoRoot
    if ($volume.ExitCode -ne 0) { throw "Could not create the isolated RabbitMQ definitions volume" }
    $definitionsVolumeCreated = $true
    Invoke-DockerCompose @("build", "test-client")
    $seed = Invoke-CapturedProcess $docker.Source @("create", "--name", $seedContainer, "--mount", "source=$definitionsVolume,target=/seed", "python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db", "sleep", "300") $repoRoot
    if ($seed.ExitCode -ne 0) { throw "Could not create the temporary definitions-volume seeder" }
    $seedContainerCreated = $true
    $copy = Invoke-CapturedProcess $docker.Source @("cp", $definitionsPath, "${seedContainer}:/seed/definitions.json") $repoRoot
    if ($copy.ExitCode -ne 0) { throw "Could not copy generated definitions into the isolated volume" }
    $removeSeed = Invoke-CapturedProcess $docker.Source @("rm", $seedContainer) $repoRoot
    if ($removeSeed.ExitCode -ne 0) { throw "Could not remove the temporary definitions-volume seeder" }
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
        if ($runtimeCreated -and (Test-Path -LiteralPath $definitionsPath)) {
            $env:PLAT05_ENV_FILE = $envPath
            $env:PLAT05_TEST_MARKER = $marker
            $env:COMPOSE_PROJECT_NAME = $projectName
            # The unique Compose project name scopes removal to this run's broker, network, and volume.
            $cleanup = Invoke-CapturedProcess $docker.Source ($composeArgs + @("down", "--volumes", "--remove-orphans")) $repoRoot
            if ($cleanup.ExitCode -ne 0) {
                Write-Warning "Compose cleanup failed; inspect only project $projectName before retrying cleanup."
            }
        }
        if ($seedContainerCreated) {
            $null = Invoke-CapturedProcess $docker.Source @("rm", "-f", $seedContainer) $repoRoot
        }
        if ($definitionsVolumeCreated) {
            $removeVolume = Invoke-CapturedProcess $docker.Source @("volume", "rm", $definitionsVolume) $repoRoot
            if ($removeVolume.ExitCode -ne 0) {
                Write-Warning "Could not remove the PLAT-05 definitions volume $definitionsVolume; verify it is unused before retrying cleanup."
            }
        }

        if ($runtimeCreated -and (Test-Path -LiteralPath $runtimeRoot)) {
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
