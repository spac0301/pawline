$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$setup = Join-Path $project 'installer\Pawline-Setup-0.2.6-x64.exe'
$temporary = Join-Path $env:RUNNER_TEMP ('Pawline install check ' + [Guid]::NewGuid())
$app = Join-Path $temporary 'app'
New-Item -ItemType Directory -Path $temporary | Out-Null
$marker = Join-Path $temporary 'existing-user-data.txt'
Set-Content $marker 'Preserve unrelated user data.'
$process = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$app`"" -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Installation failed: $($process.ExitCode)" }
$count = 0
$source = Join-Path $project 'dist'
foreach ($file in Get-ChildItem $source -File -Recurse) {
    $relative = [System.IO.Path]::GetRelativePath($source, $file.FullName)
    $installed = Join-Path $app $relative
    if (-not (Test-Path $installed)) { throw "Missing installed file: $relative" }
    if ((Get-FileHash $file.FullName).Hash -ne (Get-FileHash $installed).Hash) { throw "Installed content differs: $relative" }
    $count += 1
}
$process = Start-Process (Join-Path $app 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Uninstall failed: $($process.ExitCode)" }
if (Test-Path (Join-Path $app 'pawline\pawline.exe')) { throw 'Installed executable remains after uninstall.' }
if (-not (Test-Path $marker)) { throw 'Unrelated user data was removed.' }
@{ passed=$true; installed_files_verified=$count; uninstalled=$true; user_data_preserved=$true; model_calls=0 } | ConvertTo-Json
