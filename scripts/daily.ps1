# The scheduled daily task: trade on paper, then publish the snapshot for the hosted dashboard.
# A failed publish (e.g. offline) never affects trading; the next day's push catches up.
# Registered by scripts\schedule-daily.ps1, which also sends its output to data\daily.log.
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"
$agent = ".venv\Scripts\agent.exe"

& $agent run-daily
$tradeResult = $LASTEXITCODE

& $agent publish-dashboard
if ($LASTEXITCODE -ne 0) { Write-Output "Dashboard publish failed (exit $LASTEXITCODE); trading is unaffected." }

exit $tradeResult
