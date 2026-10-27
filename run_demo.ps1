# Starts the seeded demo on Windows: the API (:8000) and the web app (:3000), each in its own window, then opens the demo hub.
# Normally you just double-click start_demo.bat, which runs this.
#   .\run_demo.ps1            clean start (re-seeds the demo data, about a minute)
#   .\run_demo.ps1 -Keep      reuse the data of the last run (fast start, keeps what you did)
#   .\run_demo.ps1 -Dev       use the Next.js dev server instead of the production build
#   .\run_demo.ps1 -NoBrowser do not open the browser
param([switch]$Keep, [switch]$Dev, [switch]$NoBrowser)

$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "The Python environment .venv was not found next to this script. See DEMO.md, section 'First time on a new computer'." }
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) { throw "Run 'npm install' inside the frontend folder first (DEMO.md)." }

# optional AI help: a line GEMINI_API_KEY=... in a file named .env next to this script (git ignores it); the key is never printed
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*(GEMINI_API_KEY|GEMINI_MODEL)\s*=\s*(.+?)\s*$') { Set-Item -Path ("Env:" + $Matches[1]) -Value $Matches[2].Trim('"').Trim("'") }
    }
}
if ($env:GEMINI_API_KEY) { Write-Host "AI help: on (Gemini key found)" } else { Write-Host "AI help: off (no GEMINI_API_KEY; see DEMO.md)" }

$keepArg = if ($Keep) { " --keep" } else { "" }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='SIH26261 API'; Set-Location '$root'; & '$py' -m harness.demo --port 8000$keepArg"

$web = if ($Dev) { "npm run dev -- -H 0.0.0.0 -p 3000" }
       else { "npm run build; npx next start -H 0.0.0.0 -p 3000" }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='SIH26261 web'; Set-Location '$root\frontend'; $web"

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Starting the demo. The first start prepares the demo data (about a minute) and the web app is built (about half a minute); please wait..."

# wait until the web app can reach the API (the API answers only after its data is ready)
$ready = $false
for ($i = 0; $i -lt 150 -and -not $ready; $i++) {
    Start-Sleep -Seconds 2
    try { $ready = (Invoke-RestMethod -Uri "http://127.0.0.1:3000/backend/health" -TimeoutSec 3).ok } catch { $ready = $false }
}
if (-not $ready) { Write-Host "Not ready after 5 minutes. Look at the two new windows for an error message." -ForegroundColor Yellow; exit 1 }

Write-Host ""
Write-Host "READY." -ForegroundColor Green
Write-Host "  Demo hub, this computer : http://localhost:3000/demo"
if ($ip) { Write-Host "  Other devices, same Wi-Fi: http://${ip}:3000   (allow Node.js through the Windows firewall when asked)" }
Write-Host "  Close the two new windows to stop the demo."
if (-not $NoBrowser) { Start-Process "http://localhost:3000/demo" }
