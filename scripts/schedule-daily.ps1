# Registers the daily paper-trading run: 06:30 Tuesday-Saturday (after each US trading day,
# Malaysia time). If the computer is off then, Windows runs it as soon as it is back on, and
# `agent run-daily` catches up any missed days. Run once from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\schedule-daily.ps1
$repo = Split-Path -Parent $PSScriptRoot
$command = "set PYTHONUTF8=1&& `"$repo\.venv\Scripts\agent.exe`" run-daily >> `"$repo\data\daily.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $command" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday, Wednesday, Thursday, Friday, Saturday -At 06:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName "Market agent daily run" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output "Registered 'Market agent daily run' (06:30 Tue-Sat). Log: $repo\data\daily.log"
