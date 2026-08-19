[CmdletBinding()]
param(
    [string]$AceStepSource = (Join-Path (Split-Path -Parent $PSScriptRoot) '..\ACE-Step')
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot '.venv-acestep-directml'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
$requirementsFile = Join-Path $AceStepSource 'requirements.txt'

if (-not (Test-Path -LiteralPath $AceStepSource -PathType Container)) {
    throw "ACE-Step source checkout not found: $AceStepSource"
}

if (-not (Test-Path -LiteralPath $requirementsFile -PathType Leaf)) {
    throw "ACE-Step requirements file not found: $requirementsFile"
}

$pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($null -eq $pythonLauncher) {
    throw 'Python launcher (py.exe) is required to create the Python 3.12 environment.'
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $pythonLauncher.Source -3.12 -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create Python 3.12 venv at $venvPath (exit $LASTEXITCODE)."
    }
}

$venvVersion = & $venvPython -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or $venvVersion.Trim() -ne '3.12') {
    throw "Expected $venvPython to use Python 3.12, got '$venvVersion'."
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip (exit $LASTEXITCODE)."
}

# torch-directml 0.2.5.dev240914 is the published CPython 3.12 Windows wheel.
# It pins torch 2.4.1 and torchvision 0.19.1; torchaudio must match torch before
# ACE-Step's unpinned requirements are installed.
& $venvPython -m pip install --pre 'torch-directml==0.2.5.dev240914'
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install torch-directml (exit $LASTEXITCODE)."
}

& $venvPython -m pip install 'torchaudio==2.4.1'
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the torch-compatible torchaudio wheel (exit $LASTEXITCODE)."
}

& $venvPython -m pip install -r $requirementsFile
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install ACE-Step dependencies (exit $LASTEXITCODE)."
}

# ACE-Step permits diffusers>=0.33.0, but newer releases register custom ops
# whose Python signatures are rejected by torch 2.4.1 during pipeline import.
& $venvPython -m pip install 'diffusers==0.33.0'
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the torch 2.4.1-compatible diffusers version (exit $LASTEXITCODE)."
}

# The runtime uses the existing local ACE-Step checkout, while --no-deps keeps
# pip from replacing the validated DirectML torch stack during editable install.
& $venvPython -m pip install --no-deps --editable $AceStepSource
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install ACE-Step from $AceStepSource (exit $LASTEXITCODE)."
}

& $venvPython (Join-Path $repoRoot 'scripts\probe_acestep_directml.py')
if ($LASTEXITCODE -ne 0) {
    throw "DirectML backend probe failed (exit $LASTEXITCODE)."
}

Write-Output "ACE-Step DirectML runtime is ready: $venvPython"
