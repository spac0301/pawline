$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$compiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
if (-not (Test-Path $compiler)) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw 'Install Inno Setup from https://jrsoftware.org/isinfo.php to build the installer.' }
    $compiler = $command.Source
}
foreach ($app in @('pawline', 'pawline-capture')) {
    $destination = Join-Path $project "dist\$app"
    if (-not (Test-Path (Join-Path $destination "$app.exe"))) { throw "Missing verified executable: $app" }
    foreach ($doc in @('README.md', 'WINDOWS.md', 'VERIFICATION.md', 'THIRD_PARTY_NOTICES.md')) {
        Copy-Item (Join-Path $project $doc) $destination
    }
}
& $compiler (Join-Path $PSScriptRoot 'pawline.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
