param(
    [switch]$AllowNpmCiToReplaceNodeModules
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$frontend = Join-Path $repoRoot "frontend"
$lockValidator = Join-Path $PSScriptRoot "validate_frontend_lockfile.cjs"
$lockRegressionTests = Join-Path $PSScriptRoot "validate_frontend_lockfile.test.cjs"
$nativeProcessHelper = Join-Path $PSScriptRoot "NativeProcess.ps1"
$npm = Get-Command npm -ErrorAction SilentlyContinue
$node = Get-Command node -ErrorAction SilentlyContinue
$failed = $false
$taskCache = $null

. $nativeProcessHelper

function Report([string]$name, [bool]$ok, [string]$detail = "") {
    $state = if ($ok) { "PASS" } else { "FAIL" }
    $suffix = ""
    if ($detail) { $suffix = " - " + $detail }
    Write-Output ($state + ": " + $name + $suffix)
}

if (-not $node -or -not $npm) {
    Report "Node.js and npm availability" $false "Install supported Node.js and npm, then rerun."
    exit 1
}

$npmCliCandidates = @(
    (Join-Path (Split-Path -Parent $npm.Source) "node_modules/npm/bin/npm-cli.js"),
    (Join-Path (Split-Path -Parent $node.Source) "node_modules/npm/bin/npm-cli.js")
)
$npmCli = $npmCliCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $npmCli) {
    Report "npm CLI entrypoint" $false "Could not locate npm-cli.js beside the installed npm or Node.js commands."
    exit 1
}

$nodeVersionResult = Invoke-CapturedProcess $node.Source @("--version") $frontend
$npmVersionResult = Invoke-CapturedProcess $node.Source @($npmCli, "--version") $frontend
$versionsAvailable = $nodeVersionResult.ExitCode -eq 0 -and $npmVersionResult.ExitCode -eq 0
if (-not $versionsAvailable) {
    Report "Node.js and npm availability" $false "Version commands failed; process diagnostics omitted."
    exit 1
}
Report "Node.js and npm availability" $true (($nodeVersionResult.StdOut.Trim()) + ", " + ($npmVersionResult.StdOut.Trim()))

$stderrProbe = Invoke-CapturedProcess $node.Source @("-e", 'process.stderr.write("informational stderr probe"); process.exit(0)') $frontend
$stderrHandlingPassed = $stderrProbe.ExitCode -eq 0 -and $stderrProbe.StdErr -match "informational stderr probe"
Report "stderr does not override a successful process exit" $stderrHandlingPassed "native stderr is captured separately"
if (-not $stderrHandlingPassed) { $failed = $true }

$registryResult = Invoke-CapturedProcess $node.Source @($npmCli, "config", "get", "registry") $frontend
$cacheResult = Invoke-CapturedProcess $node.Source @($npmCli, "config", "get", "cache") $frontend
$registry = $registryResult.StdOut.Trim()
$cache = $cacheResult.StdOut.Trim()
$registryPublic = $registry -eq "https://registry.npmjs.org/"
$registryReady = $registryResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($registry)
$cacheReady = $cacheResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($cache)
Report "npm registry configured" $registryReady $(if ($registryPublic) { "public registry" } else { "custom registry (URL hidden)" })
Report "npm cache path resolved" $cacheReady "cache path hidden"
if (-not $registryReady -or -not $cacheReady) { $failed = $true }
foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")) {
    $set = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))
    Report "proxy variable $name" $true $(if ($set) { "set (value hidden)" } else { "unset" })
}

try {
    Push-Location $frontend
    $regressionResult = Invoke-CapturedProcess $node.Source @("--test", $lockRegressionTests) $frontend
    $regressionPassed = $regressionResult.ExitCode -eq 0
    Report "frontend lockfile regression tests" $regressionPassed $(if ($regressionPassed) { "5 tests passed" } else { "Node test runner failed; details omitted" })
    if (-not $regressionPassed) { $failed = $true }

    # Windows PowerShell 5.1 ConvertFrom-Json cannot materialize npm's packages[""] root key.
    # Node's JSON.parse preserves that valid empty-name property and checks the lock directly.
    $lockResult = Invoke-CapturedProcess $node.Source @($lockValidator, (Join-Path $frontend "package.json"), (Join-Path $frontend "package-lock.json")) $frontend
    $lockMatches = $lockResult.ExitCode -eq 0
    Report "package.json and lockfile root dependencies match" $lockMatches $(if ($lockMatches) { "validated with Node.js" } else { "lockfile validation failed; details omitted" })
    if (-not $lockMatches) { $failed = $true }

    if (-not $registryReady) {
        Report "npm registry connectivity" $false "cannot check connectivity because registry configuration failed"
        $failed = $true
    } else {
        $pingResult = Invoke-CapturedProcess $node.Source @($npmCli, "ping") $frontend
        if ($pingResult.ExitCode -ne 0) {
            Report "npm registry connectivity" $false "could not reach the configured registry; npm diagnostics omitted"
            $failed = $true
        } else {
            Report "npm registry connectivity" $true
        }
    }

    $modulesPath = Join-Path $frontend "node_modules"
    if ((Test-Path -LiteralPath $modulesPath) -and -not $AllowNpmCiToReplaceNodeModules) {
        Report "npm ci" $false "node_modules exists; rerun with -AllowNpmCiToReplaceNodeModules to let npm ci replace it."
        $failed = $true
    } elseif (-not $failed) {
        $taskCache = Join-Path $env:TEMP ("tankvitals-npm-cache-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $taskCache | Out-Null
        $installResult = Invoke-CapturedProcess $node.Source @($npmCli, "ci", "--cache", $taskCache, "--prefer-online") $frontend
        if ($installResult.ExitCode -ne 0) {
            Report "npm ci" $false "installation failed; npm diagnostics omitted to avoid exposing registry credentials"
            $failed = $true
        } else {
            Report "npm ci" $true
            $buildResult = Invoke-CapturedProcess $node.Source @($npmCli, "run", "build") $frontend
            if ($buildResult.ExitCode -ne 0) {
                Report "TypeScript and production build" $false "build failed; captured output omitted"
                $failed = $true
            } else { Report "TypeScript and production build" $true }
            $testScriptResult = Invoke-CapturedProcess $node.Source @($lockValidator, "--has-test-script") $frontend
            $testScriptCheck = $testScriptResult.ExitCode -eq 0
            if (-not $testScriptCheck) {
                Report "frontend test script discovery" $false "could not inspect package.json with Node.js"
                $failed = $true
            } elseif ($testScriptResult.StdOut.Trim() -eq "true") {
                $frontendTestResult = Invoke-CapturedProcess $node.Source @($npmCli, "test") $frontend
                if ($frontendTestResult.ExitCode -ne 0) { Report "frontend tests" $false "test command failed; captured output omitted"; $failed = $true }
                else { Report "frontend tests" $true }
            } else { Report "frontend test script" $true "No test script is defined." }
        }
    }
} finally {
    Pop-Location
    if ($taskCache -and (Test-Path -LiteralPath $taskCache)) {
        $tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
        $resolvedCache = [System.IO.Path]::GetFullPath($taskCache)
        if ($resolvedCache.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            [System.IO.Path]::GetFileName($resolvedCache).StartsWith("tankvitals-npm-cache-", [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedCache -Recurse -Force
        }
    }
}
if ($failed) { exit 1 }
