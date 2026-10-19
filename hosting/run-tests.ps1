# Run the test suite in the container: no network, code and tests mounted read-only.
# Usage (PowerShell):  .\hosting\run-tests.ps1   -> log in hosting\gen\pytest.log
$here = $PSScriptRoot
$log = Join-Path $here "gen\pytest.log"
docker run --rm --network none `
  -v "${here}\app:/app/hosting_app:ro" `
  -v "${here}\tests:/app/tests:ro" `
  viewer-test:pinned python -m pytest -q -p no:cacheprovider /app/tests 2>&1 | Out-File -Encoding utf8 $log
"exit=$LASTEXITCODE" | Add-Content -Encoding utf8 $log
