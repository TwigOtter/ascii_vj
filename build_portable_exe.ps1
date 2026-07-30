#!/usr/bin/env pwsh
param(
	[string]$PythonExe = "",
	[string]$AppName = "TwigsAsciiVJConverter",
	[switch]$Clean
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Resolve-PythonExe {
	param([string]$RequestedPythonExe)

	if ($RequestedPythonExe) {
		return $RequestedPythonExe
	}

	$pythonCandidates = @()

	if ($env:VIRTUAL_ENV) {
		$pythonCandidates += (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")
	}

	$pythonCommandNames = @("py", "python", "python3")
	foreach ($commandName in $pythonCommandNames) {
		$command = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
		if ($command) {
			$pythonCandidates += $command.Source
		}
	}

	if ($env:LOCALAPPDATA) {
		$pythonCandidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe")
		$pythonCandidates += (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\python.exe")
		$pythonCandidates += (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\python3.exe")
	}

	$pythonCandidates = $pythonCandidates | Where-Object { $_ } | Select-Object -Unique
	foreach ($candidate in $pythonCandidates) {
		try {
			& $candidate --version *> $null
			if ($LASTEXITCODE -eq 0) {
				return $candidate
			}
		} catch {
		}
	}

	return $null
}

$PythonExe = Resolve-PythonExe -RequestedPythonExe $PythonExe
if (-not $PythonExe) {
	Write-Error "Python was not found. Pass -PythonExe or install Python 3 with the py launcher or python on PATH."
	exit 1
}

Write-Host "Using Python: $PythonExe" -ForegroundColor Cyan

$pythonBaseArgs = @()
if ([System.IO.Path]::GetFileNameWithoutExtension($PythonExe).ToLowerInvariant() -eq "py") {
	$pythonBaseArgs += "-3"
}

if ($Clean) {
	Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
	Get-ChildItem -Filter "*.spec" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
& $PythonExe @pythonBaseArgs -m pip install --upgrade pyinstaller imageio-ffmpeg opencv-python pillow numpy | Out-Host
if ($LASTEXITCODE -ne 0) {
	Write-Error "Failed to install build dependencies."
	exit $LASTEXITCODE
}

$arguments = @(
	"-m", "PyInstaller",
	"--noconfirm",
	"--clean",
	"--onefile",
	"--windowed",
	"--name", $AppName,
	"--paths", $repoRoot,
	"--hidden-import", "Converter.ascii_vj",
	"--hidden-import", "Remux.audio_remux_simple",
	"--hidden-import", "PIL._tkinter_finder",
	"--collect-all", "imageio_ffmpeg",
	"--collect-all", "cv2",
	"--collect-all", "PIL",
	"$repoRoot\ascii_vj_ui.py"
)

Write-Host "Building portable EXE..." -ForegroundColor Cyan
& $PythonExe @pythonBaseArgs @arguments | Out-Host
if ($LASTEXITCODE -ne 0) {
	Write-Error "PyInstaller build failed."
	exit $LASTEXITCODE
}

$distExe = Join-Path $repoRoot "dist\$AppName.exe"
if (-not (Test-Path $distExe)) {
	Write-Error "Build completed but EXE was not found at $distExe"
	exit 1
}

Write-Host "Portable EXE created:" -ForegroundColor Green
Write-Host $distExe -ForegroundColor Green
