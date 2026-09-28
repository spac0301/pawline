$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Build Windows executables on Windows.' }
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the private runtime using WINDOWS.md first.' }
Push-Location $project
try {
    & $python -m pip install 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
    & $python -m PyInstaller --onedir --noupx --windowed --name pawline --paths . --paths vendor/claude-pet --add-data 'assets:assets' --add-data 'vendor/claude-pet:vendor/claude-pet' windows/pet_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Pet build failed.' }
    & $python -m PyInstaller --onedir --noupx --console --name pawline-capture --paths . windows/capture_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Capture build failed.' }
    foreach ($app in @('pawline', 'pawline-capture')) {
        $destination = Join-Path $project "dist\$app"
        foreach ($doc in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'PET_ASSET_NOTICE.md', 'README.md', 'WINDOWS.md', 'SECURITY.md')) {
            Copy-Item (Join-Path $project $doc) $destination
        }
        $licenses = Join-Path $destination 'licenses'
        New-Item -ItemType Directory -Force $licenses | Out-Null
        Copy-Item (Join-Path $project 'vendor\claude-pet\LICENSE') (Join-Path $licenses 'claude-pet-LICENSE')
        Copy-Item (Join-Path $project 'UPSTREAM-DETECTOR-LICENSE') $licenses
        Copy-Item (Join-Path $project 'assets\fonts\Pretendard-LICENSE.txt') $licenses
        Copy-Item (Join-Path $project 'assets\providers\OPENAI-APPS-SDK-UI-LICENSE') $licenses
        Copy-Item (Join-Path $project 'assets\providers\SOURCES.md') (Join-Path $licenses 'provider-sources.md')
    }
    Write-Output 'Built under dist. Run receiver checks before sharing these binaries.'
} finally { Pop-Location }
