function ConvertTo-NativeProcessArgument([string]$Argument) {
    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') { return $Argument }

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
        } elseif ($character -eq '"') {
            if ($backslashes -gt 0) { [void]$builder.Append(('\' * (2 * $backslashes)))}
            [void]$builder.Append('\"')
            $backslashes = 0
        } else {
            if ($backslashes -gt 0) { [void]$builder.Append(('\' * $backslashes)) }
            [void]$builder.Append($character)
            $backslashes = 0
        }
    }
    if ($backslashes -gt 0) { [void]$builder.Append(('\' * (2 * $backslashes))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-CapturedProcess(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory = (Get-Location).Path,
    [int]$TimeoutSeconds = 0,
    [switch]$StreamOutput,
    [string]$StreamPrefix = "process"
) {
    $process = New-Object System.Diagnostics.Process
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeProcessArgument $_ }) -join ' ')
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process.StartInfo = $startInfo

    try {
        if (-not $process.Start()) {
            return [pscustomobject]@{ ExitCode = 127; StdOut = ''; StdErr = '' }
        }
        # Read both redirected pipes concurrently, line by line. Pump on this
        # thread so progress can be written to the PowerShell host in real time.
        $stdout = New-Object System.Text.StringBuilder
        $stderr = New-Object System.Text.StringBuilder
        $stdoutTask = $process.StandardOutput.ReadLineAsync()
        $stderrTask = $process.StandardError.ReadLineAsync()
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        $timedOut = $false
        $drainUntil = $null
        while ($true) {
            foreach ($stream in @(
                [pscustomobject]@{ Name = "stdout"; Task = $stdoutTask },
                [pscustomobject]@{ Name = "stderr"; Task = $stderrTask }
            )) {
                if (-not $stream.Task -or -not $stream.Task.IsCompleted) { continue }
                $line = $stream.Task.GetAwaiter().GetResult()
                if ($null -eq $line) {
                    if ($stream.Name -eq "stdout") { $stdoutTask = $null } else { $stderrTask = $null }
                    continue
                }
                if ($stream.Name -eq "stdout") {
                    [void]$stdout.AppendLine($line)
                    if ($StreamOutput) { Write-Host "[$StreamPrefix] $line" }
                    $stdoutTask = $process.StandardOutput.ReadLineAsync()
                } else {
                    [void]$stderr.AppendLine($line)
                    if ($StreamOutput) { Write-Host "[$StreamPrefix] $line" }
                    $stderrTask = $process.StandardError.ReadLineAsync()
                }
            }

            if (-not $timedOut -and $TimeoutSeconds -gt 0 -and $timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                $timedOut = $true
                # Kill the process tree when the runtime supports it. Windows PowerShell
                # on older .NET falls back to killing the directly launched process.
                try {
                    $treeKill = [System.Diagnostics.Process].GetMethod("Kill", [type[]]@([bool]))
                    if ($treeKill) { $treeKill.Invoke($process, @($true)) }
                    else { $process.Kill() }
                } catch { try { $process.Kill() } catch { } }
                [void]$process.WaitForExit(5000)
                $drainUntil = (Get-Date).AddSeconds(5)
            }

            if ($process.HasExited -and -not $stdoutTask -and -not $stderrTask) { break }
            if ($timedOut -and (Get-Date) -ge $drainUntil) { break }
            Start-Sleep -Milliseconds 50
        }
        return [pscustomobject]@{
            ExitCode = if ($timedOut) { 124 } elseif ($process.HasExited) { $process.ExitCode } else { 127 }
            TimedOut = $timedOut
            StdOut = $stdout.ToString()
            StdErr = $stderr.ToString()
        }
    } catch {
        # Do not expose process exception text; it can include arguments or paths.
        return [pscustomobject]@{ ExitCode = 127; TimedOut = $false; StdOut = ''; StdErr = '' }
    } finally {
        $process.Dispose()
    }
}
