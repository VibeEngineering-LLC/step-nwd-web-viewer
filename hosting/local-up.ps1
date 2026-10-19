# Local run of the whole service (caddy + app + worker) at https://localhost
# Usage (PowerShell):  .\hosting\local-up.ps1
# Stop:  docker compose -f hosting\docker-compose.yml down      (add -v to wipe data)
$here = $PSScriptRoot
Set-Location $here
docker compose up -d --build
Start-Sleep -Seconds 8
docker compose ps
"--- invite link (path part only; host is https://localhost) ---"
docker compose exec app viewerctl invite add "local-test"
"Open the printed /u/<token> link in the browser. A self-signed certificate warning is expected on localhost."
