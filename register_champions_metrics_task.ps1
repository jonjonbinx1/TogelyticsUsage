param(
    [ValidateSet('Daily', 'Weekly')]
    [string]$Frequency = 'Daily',

    [string]$Time = '08:00',

    [ValidateSet('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')]
    [string]$DayOfWeek = 'Sunday',

    [string]$TaskName = 'ChampionsPikalyticsMetrics',

    [string]$PythonExe = 'python',

    [Alias('OutputCsv')]
    [string]$OutputBase = 'results\web_metrics\champions_web_metrics.csv',

    [string]$Url = 'https://pikalytics.com/pokedex/gen9championsvgc2026regma',

    [string]$Source = 'pikalytics'
)

$scriptPath = Join-Path $PSScriptRoot 'collect_champions_web_metrics.py'
if (-not (Test-Path $scriptPath)) {
    throw "Collector script not found: $scriptPath"
}

if ([System.IO.Path]::IsPathRooted($OutputBase)) {
    $outputPath = $OutputBase
} else {
    $outputPath = Join-Path $PSScriptRoot $OutputBase
}

$taskArgs = @(
    '"' + $scriptPath + '"',
    '--source',
    '"' + $Source + '"',
    '--url',
    '"' + $Url + '"',
    '--output',
    '"' + $outputPath + '"'
) -join ' '

$taskTime = [datetime]::ParseExact($Time, 'HH:mm', $null)
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $taskArgs -WorkingDirectory $PSScriptRoot

if ($Frequency -eq 'Daily') {
    $trigger = New-ScheduledTaskTrigger -Daily -At $taskTime
} else {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $taskTime
}

$taskRegistration = @{
    TaskName = $TaskName
    Action = $action
    Trigger = $trigger
    Description = 'Fetch Pokemon Champions web metrics from Pikalytics into separate chart-friendly CSV datasets.'
    Force = $true
}

Register-ScheduledTask @taskRegistration | Out-Null

$outputDirectory = Split-Path -Parent $outputPath
$outputStem = [System.IO.Path]::GetFileNameWithoutExtension($outputPath)

Write-Host "Registered scheduled task '$TaskName'"
Write-Host "Frequency : $Frequency"
if ($Frequency -eq 'Weekly') {
    Write-Host "Day       : $DayOfWeek"
}
Write-Host "Time      : $Time"
Write-Host "Python    : $PythonExe"
Write-Host "Output    : $outputPath"
Write-Host "Generated : $(Join-Path $outputDirectory ($outputStem + '_pokemon_usage.csv'))"
Write-Host "Generated : $(Join-Path $outputDirectory ($outputStem + '_pokemon_team_metrics.csv'))"
Write-Host "Generated : $(Join-Path $outputDirectory ($outputStem + '_team_cores.csv'))"
Write-Host "Generated : $(Join-Path $outputDirectory ($outputStem + '_top_teams.csv'))"
Write-Host "Generated : $(Join-Path $outputDirectory ($outputStem + '_team_pokemon_details.csv'))"
Write-Host "Generated : $(Join-Path $outputDirectory ($outputStem + '_team_combinations.csv'))"