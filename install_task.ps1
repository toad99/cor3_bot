# Crée les tâches planifiées du bot (pas besoin d'être administrateur). Depuis le dossier du bot :
#   powershell -ExecutionPolicy Bypass -File .\install_task.ps1
#  - « cor3_bot »      : passage complet tous les jours à 02:15 (heure de Windows, donc Paris) ;
#                        réveille le PC si besoin.
#  - « cor3_bot_auto » : tous les quarts d'heure (hh:05, 20, 35, 50) ; ne lance Chrome que si quelque chose est dû
#                        (daily pas faite, minuteur de marché échu, expédition en cours).
#                        Pour ne pas la créer : ajoute -NoAuto à la commande ci-dessus.
param([switch]$NoAuto)
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $dir "run_cor3.bat"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At "02:15"
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "cor3_bot" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "cor3.gg : daily, marchés, jobs et mercenaires (passage complet)" -Force | Out-Null
Write-Host "Tâche « cor3_bot » créée : tous les jours à 02:15."

if (-not $NoAuto) {
    $autoAction = New-ScheduledTaskAction -Execute $bat -Argument "--auto" -WorkingDirectory $dir
    # Déclencheur quotidien répété tous les quarts d'heure : 00:05, 00:20 ... 23:50 (jamais 02:15, l'heure du
    # passage complet). Chaque minuteur de marché repart quand on l'ouvre : le prendre au plus tôt = plus de jobs.
    $autoTrigger = New-ScheduledTaskTrigger -Daily -At "00:05"
    $autoTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At "00:05" `
        -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 23 -Minutes 46)).Repetition
    $autoSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "cor3_bot_auto" -Action $autoAction -Trigger $autoTrigger -Settings $autoSettings `
        -Principal $principal -Description "cor3.gg : passage tous les quarts d'heure, seulement si quelque chose est dû" -Force | Out-Null
    Write-Host "Tâche « cor3_bot_auto » créée : tous les quarts d'heure (hh:05, 20, 35, 50)."
}
Write-Host "Test immédiat : Start-ScheduledTask -TaskName cor3_bot"
