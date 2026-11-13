$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$configPath = Join-Path $projectRoot "freezercheckbis.config.json"
$loginUrl = "http://127.0.0.1:5000/login"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found at $python. Create .venv and install requirements.txt first."
}

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Configuration file not found at $configPath. Copy freezercheckbis.config.example.json to freezercheckbis.config.json and configure it first."
}

$listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port 5000 is already in use. Stop the existing freezer app before launching another copy."
}

$stateDirectory = Join-Path $env:LOCALAPPDATA "FreezerCheckBIS"
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$secretPath = Join-Path $stateDirectory "flask_secret_key.txt"
if (-not (Test-Path -LiteralPath $secretPath)) {
    $secret = & $python -c "import secrets; print(secrets.token_urlsafe(48))"
    if ($LASTEXITCODE -ne 0 -or -not $secret) {
        throw "Could not generate the Flask session signing key."
    }
    Set-Content -LiteralPath $secretPath -Value $secret -Encoding ASCII -NoNewline
}

$env:FLASK_SECRET_KEY = (Get-Content -LiteralPath $secretPath -Raw).Trim()
$env:FREEZERCHECKBIS_CONFIG = $configPath
$env:OPENBIS_ALLOW_MAIN_WRITES = "0"
$env:FLASK_COOKIE_SECURE = "0"

$server = Start-Process `
    -FilePath $python `
    -ArgumentList @("freezer_check_bis_app.py") `
    -WorkingDirectory $projectRoot `
    -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $server.Refresh()
    if ($server.HasExited) {
        throw "The freezer app stopped during startup. Check the app console for the error."
    }
    try {
        $response = Invoke-WebRequest -Uri $loginUrl -UseBasicParsing -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $ready) {
    throw "The freezer app did not become ready. Check its console for errors."
}

Start-Process $loginUrl
Write-Host "Freezer temperature app started. Close its Python console to stop the server."
