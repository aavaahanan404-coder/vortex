# Starts the seeded demo on Windows: the API (:8000) and the web app (:3000), each in its own window.
#   .\run_demo.ps1            first run or a clean start (re-seeds the demo data, about a minute)
#   .\run_demo.ps1 -Keep      reuse the data of the last run (fast start, keeps what you did)
#   .\run_demo.ps1 -Dev       use the Next.js dev server instead of the production build
# Open http://localhost:3000 ; other devices on the same network use the address printed at the end.
param([switch]$Keep, [switch]$Dev)

$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "The Python environment .venv was not found next to this script. See DEMO.md, section 'First time on a new computer'." }
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) { throw "Run 'npm install' inside the frontend folder first (DEMO.md)." }

$keepArg = if ($Keep) { " --keep" } else { "" }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='SIH26261 API'; Set-Location '$root'; & '$py' -m harness.demo --port 8000$keepArg"

$web = if ($Dev) { "npm run dev -- -H 0.0.0.0 -p 3000" }
       else { "if (-not (Test-Path .next\BUILD_ID)) { npm run build }; npx next start -H 0.0.0.0 -p 3000" }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='SIH26261 web'; Set-Location '$root\frontend'; $web"

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Starting. The API window prints the demo tokens once seeding is finished (about a minute on a fresh start)."
Write-Host "  On this computer : http://localhost:3000"
if ($ip) { Write-Host "  On the same Wi-Fi: http://${ip}:3000   (allow Node.js through the Windows firewall when asked)" }
Write-Host "Close the two new windows to stop the demo."
