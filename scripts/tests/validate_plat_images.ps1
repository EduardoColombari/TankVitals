param([string]$Docker = "docker")

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$backendDockerfile = Join-Path $repoRoot "backend/Dockerfile"
$frontendDockerfile = Join-Path $repoRoot "frontend/Dockerfile"
$nginxConfig = Join-Path $repoRoot "frontend/nginx.conf"
$errors = [System.Collections.Generic.List[string]]::new()
. (Join-Path $PSScriptRoot "NativeProcess.ps1")

function Check([string]$label, [bool]$ok, [string]$detail = "") {
    $state = if ($ok) { "PASS" } else { "FAIL" }
    $suffix = ""
    if ($detail) { $suffix = " - " + $detail }
    Write-Output ($state + ": " + $label + $suffix)
    if (-not $ok) { $script:errors.Add($label) }
}

function Get-ProcessDetail($Result) {
    $parts = @($Result.StdOut.Trim(), $Result.StdErr.Trim()) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    if ($parts.Count -gt 0) { return ($parts -join " | ") }
    return "process returned no diagnostic text"
}

function Get-HttpStatus([string]$Url) {
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = 2000
    try {
        $response = $request.GetResponse()
        try { return [int]$response.StatusCode } finally { $response.Close() }
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            try { return [int]$_.Exception.Response.StatusCode } finally { $_.Exception.Response.Close() }
        }
        return 0
    }
}

$backendText = Get-Content -Raw -LiteralPath $backendDockerfile
$frontendText = Get-Content -Raw -LiteralPath $frontendDockerfile
$nginxText = Get-Content -Raw -LiteralPath $nginxConfig
Check "backend image declares non-root runtime user" ($backendText -match '(?m)^USER\s+app\s*$')
Check "frontend runtime uses unprivileged Nginx" ($frontendText -match 'nginx-unprivileged')
Check "frontend listens on unprivileged port 8080" ($frontendText -match '(?m)^EXPOSE\s+8080\s*$' -and $nginxText -match 'listen\s+8080')
Check "API proxy targets backend" ($nginxText -match 'location\s+/api/' -and $nginxText -match 'proxy_pass\s+http://backend:8000')
Check "assistant response buffering is disabled" ($nginxText -match 'location\s+/api/assistant\s*\{[^}]*proxy_buffering\s+off')
Check "WebSocket upgrade headers are forwarded" ($nginxText -match 'location\s+/ws/\s*\{[^}]*proxy_http_version\s+1\.1[^}]*proxy_set_header\s+Upgrade[^}]*proxy_set_header\s+Connection')
Check "upstream failures use Nginx handling" ($nginxText -match 'proxy_pass\s+http://backend:8000' -and $nginxText -match 'location\s+/\s*\{') "Nginx defaults return 502/504 when upstream is unavailable."

$dockerCommand = Get-Command $Docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) {
    Check "Docker Engine access" $false "Docker CLI was not found."
} else {
    $dockerInfo = Invoke-CapturedProcess $dockerCommand.Source @("info") $repoRoot
    if ($dockerInfo.ExitCode -ne 0) {
        Check "Docker Engine access" $false "Docker Engine access failed or was denied; details omitted."
    } else {
        Check "Docker Engine access" $true
        $suffix = [guid]::NewGuid().ToString("N").Substring(0, 10)
        $images = @(
            @{ Name = "backend"; Dockerfile = $backendDockerfile; Context = (Join-Path $repoRoot "backend"); Tag = "tv-plat-backend-check-$suffix" },
            @{ Name = "frontend"; Dockerfile = $frontendDockerfile; Context = (Join-Path $repoRoot "frontend"); Tag = "tv-plat-frontend-check-$suffix" }
        )
        foreach ($image in $images) {
            $buildResult = Invoke-CapturedProcess $dockerCommand.Source @("build", "-f", $image.Dockerfile, "-t", $image.Tag, $image.Context) $repoRoot
            if ($buildResult.ExitCode -ne 0) {
                Check "$($image.Name) image build" $false "Docker build failed; diagnostic output omitted."
                continue
            }
            Check "$($image.Name) image build" $true
            try {
            if ($image.Name -eq "frontend") {
                $network = "tv-plat-nginx-$suffix"
                $backendStub = "tv-plat-backend-stub-$suffix"
                $frontendContainer = "tv-plat-frontend-run-$suffix"
                $networkCreated = $false
                $backendStubCreated = $false
                $frontendContainerCreated = $false
                try {
                    $networkResult = Invoke-CapturedProcess $dockerCommand.Source @("network", "create", $network) $repoRoot
                    $networkCreated = $networkResult.ExitCode -eq 0
                    Check "isolated Nginx validation network" $networkCreated $(if ($networkCreated) { "created" } else { Get-ProcessDetail $networkResult })

                    if ($networkCreated) {
                        # Provide the production service DNS name inside a private network.
                        # This stub deliberately has no listener on backend:8000, allowing
                        # the runtime probe to verify Nginx returns 502 for an unavailable API.
                        $stubResult = Invoke-CapturedProcess $dockerCommand.Source @(
                            "run", "-d", "--name", $backendStub, "--network", $network,
                            "--network-alias", "backend", $image.Tag, "sh", "-c", "while :; do sleep 3600; done"
                        ) $repoRoot
                        $backendStubCreated = $stubResult.ExitCode -eq 0
                        Check "isolated backend DNS alias" $backendStubCreated $(if ($backendStubCreated) { "backend resolves on the validation network; port 8000 intentionally has no listener" } else { Get-ProcessDetail $stubResult })
                    }

                    if ($networkCreated -and $backendStubCreated) {
                        $frontResult = Invoke-CapturedProcess $dockerCommand.Source @(
                            "run", "-d", "--name", $frontendContainer, "--network", $network,
                            "-p", "127.0.0.1::8080", $image.Tag
                        ) $repoRoot
                        $frontendContainerCreated = $frontResult.ExitCode -eq 0
                        Check "frontend container starts in the isolated network" $frontendContainerCreated $(if ($frontendContainerCreated) { "started" } else { Get-ProcessDetail $frontResult })
                    }

                    if ($frontendContainerCreated) {
                        $runningResult = Invoke-CapturedProcess $dockerCommand.Source @("inspect", "--format", "{{.State.Running}}", $frontendContainer) $repoRoot
                        $containerRunning = $runningResult.ExitCode -eq 0 -and $runningResult.StdOut.Trim() -eq "true"
                        Check "frontend Nginx process starts" $containerRunning $(if ($containerRunning) { "running" } else { Get-ProcessDetail $runningResult })

                        if ($containerRunning) {
                            $nginxResult = Invoke-CapturedProcess $dockerCommand.Source @("exec", $frontendContainer, "nginx", "-t") $repoRoot
                            Check "Nginx configuration syntax" ($nginxResult.ExitCode -eq 0) $(if ($nginxResult.ExitCode -eq 0) { "nginx -t passed" } else { "nginx -t failed (exit $($nginxResult.ExitCode)): $(Get-ProcessDetail $nginxResult)" })

                            $runtimeUser = Invoke-CapturedProcess $dockerCommand.Source @("exec", $frontendContainer, "id", "-u") $repoRoot
                            $runtimeNonRoot = $runtimeUser.ExitCode -eq 0 -and $runtimeUser.StdOut.Trim() -ne "0"
                            Check "frontend runtime process is non-root" $runtimeNonRoot $(if ($runtimeNonRoot) { $runtimeUser.StdOut.Trim() } else { Get-ProcessDetail $runtimeUser })

                            $portResult = Invoke-CapturedProcess $dockerCommand.Source @("port", $frontendContainer, "8080/tcp") $repoRoot
                            $portMatch = [regex]::Match($portResult.StdOut, '127\.0\.0\.1:(\d+)')
                            $hostPort = if ($portMatch.Success) { $portMatch.Groups[1].Value } else { $null }
                            Check "frontend port 8080 is reachable on loopback" ($portResult.ExitCode -eq 0 -and $hostPort) $(if ($hostPort) { "127.0.0.1:$hostPort -> container:8080" } else { Get-ProcessDetail $portResult })

                            if ($hostPort) {
                                $rootStatus = 0
                                $deadline = [DateTime]::UtcNow.AddSeconds(30)
                                while ($rootStatus -eq 0 -and [DateTime]::UtcNow -lt $deadline) {
                                    $rootStatus = Get-HttpStatus "http://127.0.0.1:$hostPort/"
                                    if ($rootStatus -eq 0) { Start-Sleep -Milliseconds 500 }
                                }
                                Check "frontend serves HTTP on port 8080" ($rootStatus -eq 200) "HTTP status $rootStatus"

                                $apiStatus = Get-HttpStatus "http://127.0.0.1:$hostPort/api/health"
                                Check "API proxy reports unavailable upstream as HTTP 502" ($apiStatus -eq 502) "HTTP status $apiStatus with backend DNS present and no API listener"
                            }
                        }
                    }
                } finally {
                    foreach ($containerName in @($frontendContainer, $backendStub)) {
                        $removeContainer = Invoke-CapturedProcess $dockerCommand.Source @("rm", "-f", $containerName) $repoRoot
                        if ($removeContainer.ExitCode -ne 0 -and (($containerName -eq $frontendContainer -and $frontendContainerCreated) -or ($containerName -eq $backendStub -and $backendStubCreated))) {
                            Write-Warning "Could not remove the uniquely named validation container $containerName."
                        }
                    }
                    if ($networkCreated) {
                        $removeNetwork = Invoke-CapturedProcess $dockerCommand.Source @("network", "rm", $network) $repoRoot
                        if ($removeNetwork.ExitCode -ne 0) { Write-Warning "Could not remove the uniquely named validation network $network." }
                    }
                }
            }
            $inspectResult = Invoke-CapturedProcess $dockerCommand.Source @("image", "inspect", "--format", "{{.Config.User}}", $image.Tag) $repoRoot
            $inspect = $inspectResult.StdOut.Trim()
            $nonRoot = $inspectResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($inspect) -and $inspect -ne "0"
            Check "$($image.Name) image runtime user is non-root" $nonRoot $(if ($nonRoot) { $inspect } else { "image user check failed; details omitted" })
            } finally {
            $removeResult = Invoke-CapturedProcess $dockerCommand.Source @("image", "rm", $image.Tag) $repoRoot
            if ($removeResult.ExitCode -ne 0) { Write-Warning "Could not remove the uniquely tagged validation image $($image.Tag)." }
            }
        }
    }
}
if ($errors.Count) { exit 1 }
