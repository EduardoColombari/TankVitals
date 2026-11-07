param(
    [string]$Python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" })
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "infra/rabbitmq/plat05/writer-integration.compose.yaml"
$project = "tvp06-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$runtime = Join-Path $env:TEMP $project
$diagnostics = Join-Path $env:TEMP "$project-diagnostics"
$envFile = Join-Path $runtime ".env"
$definitions = Join-Path $runtime "secrets/definitions.json"
$volume = "$project-definitions"
$seeder = "$project-seeder"
$restartValue = Get-Random -Minimum 100000000 -Maximum 2000000000
$docker = Get-Command docker -ErrorAction SilentlyContinue
$composeExe = $null
$dockerExe = if ($docker) { $docker.Source } else { $null }
$volumeCreated = $false
$seederCreated = $false
$runtimeCreated = $false
$suiteDeadline = (Get-Date).AddMinutes(15)
$script:cleanupMode = $false
$environmentNames = @("PLAT06_ENV_FILE", "PLAT06_DEFINITIONS_VOLUME", "COMPOSE_PROJECT_NAME", "PLAT06_RESTART_VALUE", "DOCKER_API_VERSION")
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
# Docker documents DOCKER_API_VERSION as an override that disables client/server
# API negotiation. Let the CLI negotiate with the host Engine during this test.
Remove-Item Env:DOCKER_API_VERSION -ErrorAction SilentlyContinue
. (Join-Path $PSScriptRoot "NativeProcess.ps1")

function Write-Scenario([string]$Name, [scriptblock]$Action) {
    $script:activeScenario = $Name
    Write-Output "RUN: scenario '$Name'"
    try {
        & $Action
        Write-Output "PASS: $Name"
    } catch {
        Write-Output ("FAIL: " + $Name + " - " + $_.Exception.Message)
        $script:scenarioFailures.Add($Name)
    }
}

function Invoke-Docker([string[]]$Arguments, [switch]$Capture, [string]$Stage = "Docker command", [int]$TimeoutSeconds = 90, [switch]$StreamOutput) {
    $remaining = [int][Math]::Floor(($script:suiteDeadline - (Get-Date)).TotalSeconds)
    if (-not $script:cleanupMode) {
        $remaining -= 45
        if ($remaining -le 0) { throw "PLAT-06 suite deadline reached before operation '$Stage'; cleanup reserved." }
        $TimeoutSeconds = [Math]::Min($TimeoutSeconds, $remaining)
    }
    Write-Host "RUN: $Stage (timeout ${TimeoutSeconds}s)"
    if ($script:composeExe) { $result = Invoke-CapturedProcess $script:composeExe $Arguments $script:repoRoot $TimeoutSeconds -StreamOutput:$StreamOutput -StreamPrefix $Stage }
    else { $result = Invoke-CapturedProcess $script:dockerExe $Arguments $script:repoRoot $TimeoutSeconds -StreamOutput:$StreamOutput -StreamPrefix $Stage }
    if ($result.ExitCode -ne 0) {
        $script:commandFailures.Add([pscustomobject]@{
            Stage = $Stage
            ExitCode = $result.ExitCode
            TimedOut = [bool]$result.TimedOut
            StdOut = $result.StdOut
            StdErr = $result.StdErr
        })
        if ($result.TimedOut) { throw "Timeout after ${TimeoutSeconds}s during '$Stage' (scenario: $($script:activeScenario)). Docker process was terminated; see redacted harness.log." }
        throw ($Stage + " failed with exit code " + $result.ExitCode + "; see redacted harness.log.")
    }
    if ($Capture) { return @($result.StdOut, $result.StdErr) }
    return $null
}

function Invoke-Compose([string[]]$Arguments, [switch]$Capture, [string]$Stage, [int]$TimeoutSeconds = 90, [switch]$StreamOutput) {
    $prefix = @("compose", "-p", $script:project, "--env-file", ($script:envFile -replace "\\", "/"), "-f", $script:composeFile)
    if ($script:composeExe) { $prefix = @("-p", $script:project, "--env-file", ($script:envFile -replace "\\", "/"), "-f", $script:composeFile) }
    if (-not $Stage) { $Stage = "Docker Compose " + $(if ($Arguments.Count -gt 0) { $Arguments[0] } else { "command" }) }
    if ($Arguments -contains "build") { $TimeoutSeconds = [Math]::Max($TimeoutSeconds, 300) }
    if ($Arguments -contains "--wait") { $TimeoutSeconds = [Math]::Max($TimeoutSeconds, 210) }
    return Invoke-Docker ($prefix + $Arguments) -Capture:$Capture -Stage $Stage -TimeoutSeconds $TimeoutSeconds -StreamOutput:$StreamOutput
}

function Invoke-TestClient([string]$Mode) {
    # `compose run` otherwise starts the writer dependency chain again. That
    # reruns the one-shot bootstrap against the already-populated token volume.
    # Its COMMAND also replaces the service command, so set an explicit entrypoint.
    $args = @(
        "run", "--rm", "--no-deps", "--entrypoint", "python",
        "test-client", "/opt/plat06/writer_influx_test.py", $Mode
    )
    Invoke-Compose $args -Stage "Docker Compose run test-client $Mode" -StreamOutput | Out-Null
}

function Save-RedactedDiagnostics {
    if (-not $script:runtimeCreated -or -not (Test-Path -LiteralPath $script:envFile)) { return }
    New-Item -ItemType Directory -Path $script:diagnostics -Force | Out-Null
    try {
        $envValues = @{}
        foreach ($line in Get-Content -LiteralPath $script:envFile) {
            if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -and $Matches[2]) { $envValues[$Matches[2]] = "[REDACTED]" }
        }
        $failureText = foreach ($failure in $script:commandFailures) {
            $stdout = [string]$failure.StdOut
            $stderr = [string]$failure.StdErr
            foreach ($secret in $envValues.Keys) {
                $stdout = $stdout.Replace($secret, "[REDACTED]")
                $stderr = $stderr.Replace($secret, "[REDACTED]")
            }
            "Stage: $($failure.Stage)`r`nExit code: $($failure.ExitCode)`r`nTimed out: $($failure.TimedOut)`r`nstdout:`r`n$stdout`r`nstderr:`r`n$stderr"
        }
        if ($failureText) {
            Set-Content -LiteralPath (Join-Path $script:diagnostics "harness.log") -Value ($failureText -join "`r`n`r`n") -Encoding utf8
        }
        try {
            $logs = Invoke-Compose @("logs", "--no-color", "--timestamps") -Capture -Stage "Collect project-scoped failure logs" -TimeoutSeconds 10
            $safeLogs = ($logs -join [Environment]::NewLine)
            foreach ($secret in $envValues.Keys) { $safeLogs = $safeLogs.Replace($secret, "[REDACTED]") }
            Set-Content -LiteralPath (Join-Path $script:diagnostics "compose.log") -Value $safeLogs -Encoding utf8
        } catch {
            Set-Content -LiteralPath (Join-Path $script:diagnostics "compose.log") -Value "Container logs unavailable; inspect harness.log for the failing command." -Encoding utf8
        }
        Write-Output "Diagnostics retained at: $script:diagnostics"
    } catch { Write-Warning "Could not retain Compose diagnostics; error details are hidden to protect credentials." }
}

$scenarioFailures = [System.Collections.Generic.List[string]]::new()
$commandFailures = [System.Collections.Generic.List[object]]::new()
try {
    if (-not $docker) { throw "Docker CLI is not installed or is unavailable in PATH." }
    Write-Output "RUN: Docker Engine info (timeout 30s)"
    $dockerInfo = Invoke-CapturedProcess $docker.Source @("info") $repoRoot 30
    if ($dockerInfo.TimedOut) { throw "Timeout after 30s during Docker Engine info (scenario: startup)." }
    if ($dockerInfo.ExitCode -ne 0) { throw "Docker Engine access is unavailable or denied; diagnostic output was omitted." }

    Write-Output "RUN: Docker Compose version (timeout 30s)"
    $composeVersionResult = Invoke-CapturedProcess $docker.Source @("compose", "version") $repoRoot 30
    if ($composeVersionResult.TimedOut) { throw "Timeout after 30s during Docker Compose version (scenario: startup)." }
    if ($composeVersionResult.ExitCode -ne 0) {
        $candidate = Join-Path $env:ProgramFiles "Docker/cli-plugins/docker-compose.exe"
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "Docker Engine is reachable but the Docker Compose plugin is unavailable." }
        $script:composeExe = $candidate
        Write-Output "RUN: Docker Compose plugin version (timeout 30s)"
        $composeVersionResult = Invoke-CapturedProcess $script:composeExe @("version") $repoRoot 30
        if ($composeVersionResult.TimedOut) { throw "Timeout after 30s during Docker Compose plugin version (scenario: startup)." }
        if ($composeVersionResult.ExitCode -ne 0) { throw "Docker Compose plugin could not start." }
    }
    Write-Output "PASS: Docker Engine and Compose available"

    if (Test-Path -LiteralPath $runtime) { throw "Refusing to reuse existing temporary path: $runtime" }
    New-Item -ItemType Directory -Path $runtime | Out-Null
    $runtimeCreated = $true
    $pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw "Python executable is unavailable." }
    Write-Output "RUN: generate isolated test secrets (timeout 30s)"
    $generation = Invoke-CapturedProcess $pythonCommand.Source @((Join-Path $repoRoot "scripts/gerar_segredos.py"), "--output-dir", $runtime) $repoRoot 30
    if ($generation.TimedOut) { throw "Timeout after 30s during secret generation (scenario: startup)." }
    if ($generation.ExitCode -ne 0) { throw "Secret generation failed; diagnostic output was omitted." }

    $env:PLAT06_ENV_FILE = ($envFile -replace "\\", "/")
    $env:PLAT06_DEFINITIONS_VOLUME = $volume
    $env:COMPOSE_PROJECT_NAME = $project
    $env:PLAT06_RESTART_VALUE = [string]$restartValue
    Invoke-Docker @("volume", "create", $volume) -Stage "Docker create isolated definitions volume" | Out-Null
    $volumeCreated = $true
    Invoke-Docker @("create", "--name", $seeder, "--mount", "source=$volume,target=/seed", "python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db", "sleep", "300") -Stage "Docker create definitions seeder" | Out-Null
    $seederCreated = $true
    Invoke-Docker @("cp", $definitions, "${seeder}:/seed/definitions.json") -Stage "Docker copy RabbitMQ definitions" | Out-Null
    Invoke-Docker @("rm", $seeder) -Stage "Docker remove definitions seeder" | Out-Null
    $seederCreated = $false

    # test-client shares the test-tools image built by influx-fault-proxy and has no build section.
    Invoke-Compose @("build", "token-bootstrap", "influx-fault-proxy") -TimeoutSeconds 300 | Out-Null
    Invoke-Compose @("up", "-d", "--wait", "rabbitmq", "influxdb", "influx-fault-proxy", "writer") | Out-Null

    Write-Scenario "Writer token is bucket-scoped" { Invoke-TestClient "token-check" }
    Write-Scenario "telemetry is persisted and acknowledged" { Invoke-TestClient "success" }
    Write-Scenario "temporary Influx failure is retried" { Invoke-TestClient "transient" }
    Write-Scenario "three failed writes reach DLQ" { Invoke-TestClient "permanent" }
    Write-Scenario "duplicate event behavior is measured" { Invoke-TestClient "duplicate" }
    Write-Scenario "Writer restart preserves and drains queued message" {
        Invoke-Compose @("stop", "writer") | Out-Null
        Invoke-TestClient "publish-only"
        Invoke-TestClient "queue-pending"
        # Keep RabbitMQ, InfluxDB, proxy, and the completed one-shot bootstrap
        # untouched. Compose otherwise evaluates writer dependencies and can
        # rerun token-bootstrap against its deliberately protected token file.
        Invoke-Compose @("up", "-d", "--no-deps", "writer") | Out-Null
        Invoke-TestClient "drain"
    }
} catch {
    Write-Output ("FAIL: integration harness - " + $_.Exception.Message)
    $scenarioFailures.Add("integration harness")
} finally {
    $script:cleanupMode = $true
    Save-RedactedDiagnostics
    if ($runtimeCreated) {
        try { Invoke-Compose @("down", "--volumes", "--remove-orphans", "--rmi", "local") -Stage "Cleanup isolated Compose project $project" -TimeoutSeconds 20 | Out-Null }
        catch { Write-Warning "Project-scoped Compose cleanup failed; inspect only resources named $project." }
    }
    if ($seederCreated) {
        try { Invoke-Docker @("rm", "-f", $seeder) -Stage "Docker cleanup definitions seeder" -TimeoutSeconds 10 | Out-Null }
        catch { Write-Warning "Could not remove test seeder $seeder; it is uniquely named for project $project." }
    }
    if ($volumeCreated) {
        try { Invoke-Docker @("volume", "rm", $volume) -Stage "Docker cleanup definitions volume" -TimeoutSeconds 10 | Out-Null }
        catch { Write-Warning "Could not remove the uniquely named definitions volume $volume." }
    }
    if ($runtimeCreated -and (Test-Path -LiteralPath $runtime)) {
        try {
            $tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
            $fullRuntime = [System.IO.Path]::GetFullPath($runtime)
            if ($fullRuntime.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and [System.IO.Path]::GetFileName($fullRuntime) -eq $project) {
                Remove-Item -LiteralPath $fullRuntime -Recurse -Force
            } else { Write-Warning "Refusing to remove unexpected runtime path $fullRuntime" }
        } catch { Write-Warning "Could not remove this run's temporary secret directory; path remains isolated at $runtime." }
    }
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process") }
}

if ($scenarioFailures.Count -gt 0) {
    Write-Output "FAIL: $($scenarioFailures.Count) integration scenario(s): $($scenarioFailures -join ', ')"
    exit 1
}
Write-Output "PASS: complete isolated PLAT-06 RabbitMQ to Writer to InfluxDB integration suite"
