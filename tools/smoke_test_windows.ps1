$ErrorActionPreference = "Stop"

$stdout = Join-Path $env:RUNNER_TEMP "parser-smoke.stdout.log"
$stderr = Join-Path $env:RUNNER_TEMP "parser-smoke.stderr.log"
$oldApiHost = $env:API_HOST
$oldApiPort = $env:API_PORT
$oldBrowser = $env:BROWSER_OPEN_ON_START
$oldAdminUser = $env:ADMIN_USERNAME
$oldAdminPassword = $env:ADMIN_PASSWORD
$env:API_HOST = "127.0.0.1"
$env:API_PORT = "18765"
$env:BROWSER_OPEN_ON_START = "0"
$env:ADMIN_USERNAME = ""
$env:ADMIN_PASSWORD = ""
$process = $null

try {
    $process = Start-Process -FilePath "dist/Parser.exe" -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
}
finally {
    $env:API_HOST = $oldApiHost
    $env:API_PORT = $oldApiPort
    $env:BROWSER_OPEN_ON_START = $oldBrowser
    $env:ADMIN_USERNAME = $oldAdminUser
    $env:ADMIN_PASSWORD = $oldAdminPassword
}

try {
    $status = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($process.HasExited) {
            throw "Parser.exe exited early with code $($process.ExitCode)"
        }
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:18765/api/bot/status" -TimeoutSec 2
            break
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    if ($null -eq $status) {
        throw "Parser.exe did not expose its API within 60 seconds"
    }
    if (-not $status.available -or $status.status -ne "stopped") {
        throw "Unexpected initial bot status: $($status | ConvertTo-Json -Compress)"
    }

    $page = Invoke-WebRequest -Uri "http://127.0.0.1:18765/" -TimeoutSec 5 -UseBasicParsing
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch "<html") {
        throw "Admin panel page did not return the expected HTML"
    }

    $favicon = Invoke-WebRequest -Uri "http://127.0.0.1:18765/favicon.png" -TimeoutSec 5 -UseBasicParsing
    if ($favicon.StatusCode -ne 200) {
        throw "Bundled favicon is not served"
    }

    Write-Host "Parser.exe smoke test passed: API, admin HTML, and favicon are reachable."
}
catch {
    if (Test-Path $stdout) { Get-Content $stdout }
    if (Test-Path $stderr) { Get-Content $stderr }
    throw
}
finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
}
