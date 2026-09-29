$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Build Windows executables on Windows.' }
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the private runtime using WINDOWS.md first.' }
Push-Location $project
try {
    $requiredPython = (Get-Content (Join-Path $PSScriptRoot 'python-version.txt') -Raw).Trim()
    & $python -c "import sys; assert sys.version.split()[0] == sys.argv[1], 'Use the pinned Windows Python version: ' + sys.argv[1]" $requiredPython
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python build runtime.' }
    & $python -m pip install 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
    & $python -c "import importlib.metadata as m; assert not any(d.metadata['Name'].lower().replace('_', '-') == 'pyside6-addons' for d in m.distributions()), 'Use a clean runtime with requirements-windows.lock; unused Qt Addons must not enter the bundle.'"
    if ($LASTEXITCODE -ne 0) { throw 'Unexpected Qt Addons in the build runtime.' }
    New-Item -ItemType Directory -Force build | Out-Null
    & $python windows/write_version_info.py 'Pawline' build/pet-version.txt
    & $python windows/write_version_info.py 'Pawline Relay' build/relay-version.txt
    if ($LASTEXITCODE -ne 0) { throw 'Windows identity generation failed.' }
    & $python -m PyInstaller --onedir --noupx --windowed --name pawline --version-file build/pet-version.txt --icon assets/app/pawline.ico --paths . --paths vendor/claude-pet --add-data 'assets/app:assets/app' --add-data 'assets/fonts:assets/fonts' --add-data 'assets/providers:assets/providers' --add-data 'assets/licenses:assets/licenses' --add-data 'vendor/claude-pet:vendor/claude-pet' windows/pet_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Pet build failed.' }
    & $python -m PyInstaller --onedir --noupx --console --name pawline-capture --version-file build/relay-version.txt --icon assets/app/pawline.ico --paths . windows/capture_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Capture build failed.' }
    & $python -c "from pathlib import Path; from fluff_monitor.observer_runtime import build_bundle; Path('dist/pawline-capture/observer-runtime.zip').write_bytes(build_bundle())"
    if ($LASTEXITCODE -ne 0) { throw 'Observer package build failed.' }
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
    & $python windows/bundle_licenses.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime license collection failed.' }
    Write-Output 'Built under dist. Run the packaged executable checks before publishing.'
} finally { Pop-Location }
