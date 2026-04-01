# GeoLabeller Windows Build Script
# Builds the application using cx_Freeze in a temporary virtual environment

param(
    [switch]$Msi,      # Build MSI installer instead of just executable
    [switch]$Clean,    # Clean build directory before building
    [switch]$KeepVenv, # Keep the virtual environment after build
    [string]$Python = "python",  # Path or command for Python executable
    [string]$Version   # Optional version string (e.g. "1.2.3")
)

$ErrorActionPreference = "Stop"

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvDir = Join-Path $ScriptDir ".build_venv"
$RequirementsFile = Join-Path $ScriptDir "requirements.txt"

Write-Host "GeoLabeller Windows Build Script" -ForegroundColor Cyan
Write-Host "=================================" -ForegroundColor Cyan
Write-Host ""

# Check for Python
Write-Host "Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonVersion = & $Python --version 2>&1
    Write-Host "  Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Please install Python and add to PATH." -ForegroundColor Red
    exit 1
}

# Clean if requested
if ($Clean) {
    Write-Host "Cleaning build directory..." -ForegroundColor Yellow
    $buildOutput = Join-Path $ScriptDir "build"
    $distOutput = Join-Path $ScriptDir "dist"
    if (Test-Path $buildOutput) {
        Remove-Item -Recurse -Force $buildOutput
    }
    if (Test-Path $distOutput) {
        Remove-Item -Recurse -Force $distOutput
    }
    if (Test-Path $VenvDir) {
        Remove-Item -Recurse -Force $VenvDir
    }
    Write-Host "  Cleaned" -ForegroundColor Green
}

# Create virtual environment
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path $VenvDir)) {
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  Virtual environment ready" -ForegroundColor Green

# Activate venv and get python path
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# Update pip, setuptools, and wheel using python -m pip
Write-Host "Updating pip, setuptools, and wheel..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip setuptools wheel 2>&1 | Out-Null

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $VenvPython -m pip install -r $RequirementsFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: Failed to install dependencies" -ForegroundColor Red
    exit 1
}
Write-Host "  Dependencies installed" -ForegroundColor Green

# Set version environment variable if provided
if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        Write-Host "  ERROR: Version must be in X.X.X format (e.g. 1.2.3)" -ForegroundColor Red
        exit 1
    }
    $env:GEOLABELLER_VERSION = $Version
    Write-Host "Version: $Version" -ForegroundColor Cyan
}

# Change to build directory
Push-Location $ScriptDir

try {
    if ($Msi) {
        Write-Host "Building MSI installer..." -ForegroundColor Yellow
        & $VenvPython setup.py bdist_msi
    } else {
        Write-Host "Building executable..." -ForegroundColor Yellow
        & $VenvPython setup.py build
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "" 
        Write-Host "Build completed successfully!" -ForegroundColor Green
        
        # Find output
        $outputDir = Get-ChildItem -Path (Join-Path $ScriptDir "build") -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "exe.*" } | Select-Object -First 1
        if ($outputDir) {
            Write-Host "Output location: $($outputDir.FullName)" -ForegroundColor Cyan
        }
        
        if ($Msi) {
            $msiFile = Get-ChildItem -Path (Join-Path $ScriptDir "dist") -Filter "*.msi" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($msiFile) {
                Write-Host "MSI Installer: $($msiFile.FullName)" -ForegroundColor Cyan
            }
        }
    } else {
        Write-Host "Build failed!" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
    
    # Clear version environment variable
    if ($env:GEOLABELLER_VERSION) {
        Remove-Item Env:GEOLABELLER_VERSION
    }

    # Clean up virtual environment unless -KeepVenv is specified
    if (-not $KeepVenv -and (Test-Path $VenvDir)) {
        Write-Host "Cleaning up virtual environment..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $VenvDir
        Write-Host "  Done" -ForegroundColor Green
    }
}
