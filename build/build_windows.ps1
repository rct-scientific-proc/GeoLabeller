# GeoLabeller Windows Build Script
# Builds the application using cx_Freeze in a temporary virtual environment.
# Can optionally produce a signed .msi installer.

param(
    [switch]$Msi,             # Build MSI installer instead of just executable
    [switch]$Shortcut,        # Also install a Desktop shortcut (Start Menu is always added)
    [switch]$Clean,           # Clean build directory before building
    [switch]$KeepVenv,        # Keep the virtual environment after build
    [switch]$Sign,            # Authenticode-sign the executable and the MSI
    [string]$CertThumbprint,  # Signing cert thumbprint (in a cert store)
    [string]$CertPath,        # ...or path to a .pfx file (alternative to thumbprint)
    [string]$CertPassword,    # Password for the .pfx file
    [string]$TimestampUrl = "http://timestamp.digicert.com",  # RFC-3161 timestamp server
    [string]$Python = "python",  # Path or command for Python executable
    [string]$Version,         # Optional version string (e.g. "1.2.3"); auto-detected if omitted
    [string]$Author,          # Publisher shown in Add/Remove Programs
    [string]$Url,             # About/help URL shown in Add/Remove Programs
    [string]$Proxy,           # pip proxy (e.g. http://proxy.corp:8080); auto-detected if omitted
    [switch]$NoProxy          # Skip proxy detection entirely (direct connection)
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

# Abort with a message if the last native command failed.
function Assert-LastExit([string]$Message) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: $Message (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

# Locate signtool.exe (PATH first, then the Windows 10/11 SDK).
function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
        (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    )
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $found = Get-ChildItem -Path $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "\\x64\\" } |
                Sort-Object FullName -Descending | Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }
    return $null
}

# Authenticode-sign a single file with SHA-256 + a trusted timestamp.
function Invoke-SignFile([string]$SignTool, [string]$FilePath) {
    $signArgs = @("sign", "/fd", "sha256", "/tr", $TimestampUrl, "/td", "sha256")
    if ($CertThumbprint) {
        $signArgs += @("/sha1", $CertThumbprint)
    } elseif ($CertPath) {
        $signArgs += @("/f", $CertPath)
        if ($CertPassword) { $signArgs += @("/p", $CertPassword) }
    } else {
        throw "Signing requested but no certificate given (-CertThumbprint or -CertPath)."
    }
    $signArgs += @($FilePath)
    Write-Host "  Signing $(Split-Path -Leaf $FilePath)..." -ForegroundColor Yellow
    & $SignTool @signArgs
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $FilePath" }
}

# Check for Python
Write-Host "Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonVersion = & $Python --version 2>&1
    Write-Host "  Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Please install Python and add to PATH." -ForegroundColor Red
    exit 1
}

# Validate signing inputs up front so we fail fast (before the long build).
$SignTool = $null
if ($Sign) {
    if (-not $CertThumbprint -and -not $CertPath) {
        Write-Host "  ERROR: -Sign requires -CertThumbprint or -CertPath." -ForegroundColor Red
        exit 1
    }
    $SignTool = Find-SignTool
    if (-not $SignTool) {
        Write-Host "  ERROR: signtool.exe not found. Install the Windows SDK or add it to PATH." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Using signtool: $SignTool" -ForegroundColor Green
}

# Determine the version: explicit -Version, else a VERSION file, else git tag.
if (-not $Version) {
    $versionFile = Join-Path $ProjectRoot "VERSION"
    if (Test-Path $versionFile) {
        $Version = (Get-Content $versionFile -Raw).Trim()
        Write-Host "Version from VERSION file: $Version" -ForegroundColor Cyan
    } else {
        try {
            $gitTag = (& git -C $ProjectRoot describe --tags --abbrev=0 2>$null)
            if ($LASTEXITCODE -eq 0 -and $gitTag) {
                $Version = $gitTag.Trim().TrimStart("v")
                Write-Host "Version from git tag: $Version" -ForegroundColor Cyan
            }
        } catch { }
    }
}
if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        Write-Host "  ERROR: Version must be in X.X.X format (e.g. 1.2.3), got '$Version'." -ForegroundColor Red
        exit 1
    }
    $env:GEOLABELLER_VERSION = $Version
    Write-Host "Version: $Version" -ForegroundColor Cyan
}

# Pass through publisher / URL metadata for Add/Remove Programs.
if ($Author) { $env:GEOLABELLER_AUTHOR = $Author }
if ($Url) { $env:GEOLABELLER_URL = $Url }

# Clean if requested
if ($Clean) {
    Write-Host "Cleaning build directory..." -ForegroundColor Yellow
    foreach ($dir in @((Join-Path $ScriptDir "build"), (Join-Path $ScriptDir "dist"), $VenvDir)) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
    Write-Host "  Cleaned" -ForegroundColor Green
}

# Create virtual environment
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path $VenvDir)) {
    & $Python -m venv $VenvDir
    Assert-LastExit "Failed to create virtual environment"
}
Write-Host "  Virtual environment ready" -ForegroundColor Green

# Activate venv and get python path
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# Determine a pip proxy: explicit -Proxy, else standard env vars, else the
# Windows (WinINET / Internet Options) proxy, else the WinHTTP proxy. Corporate
# machines usually configure the proxy in Internet Options rather than env vars,
# which is why pip needs it passed explicitly.
function Get-PipProxy {
    if ($Proxy) { return $Proxy }

    # Standard proxy environment variables (pip honours these, but be explicit).
    foreach ($name in 'HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy') {
        $val = [Environment]::GetEnvironmentVariable($name)
        if ($val) { return $val }
    }

    # Windows (WinINET / IE / Edge) proxy from the registry.
    try {
        $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        $s = Get-ItemProperty -Path $key -ErrorAction Stop
        if ($s.ProxyEnable -eq 1 -and $s.ProxyServer) {
            $server = [string]$s.ProxyServer
            if ($server -match '=') {
                # Per-protocol list like "http=host:port;https=host:port".
                $map = @{}
                foreach ($pair in $server -split ';') {
                    $kv = $pair -split '=', 2
                    if ($kv.Count -eq 2) { $map[$kv[0].Trim().ToLower()] = $kv[1].Trim() }
                }
                if ($map['https']) { return $map['https'] }
                if ($map['http'])  { return $map['http'] }
            } else {
                return $server
            }
        }
    } catch { }

    # WinHTTP proxy (netsh) as a last resort.
    try {
        $line = (netsh winhttp show proxy 2>$null) | Where-Object { $_ -match 'Proxy Server' }
        if ($line -and ($line -match '(\S+:\d+)')) { return $Matches[1] }
    } catch { }

    return $null
}

$pipProxyArgs = @()
if (-not $NoProxy) {
    $pipProxy = Get-PipProxy
    if ($pipProxy) {
        # pip wants a scheme; add one if the detected value lacks it.
        if ($pipProxy -notmatch '://') { $pipProxy = "http://$pipProxy" }
        $pipProxyArgs = @('--proxy', $pipProxy)
        Write-Host "Using pip proxy: $pipProxy" -ForegroundColor Cyan
    } else {
        Write-Host "No proxy detected (using a direct connection)." -ForegroundColor DarkGray
    }
}

# Update pip, setuptools, and wheel using python -m pip
Write-Host "Updating pip, setuptools, and wheel..." -ForegroundColor Yellow
& $VenvPython -m pip install @pipProxyArgs --upgrade pip setuptools wheel
Assert-LastExit "Failed to update pip/setuptools/wheel"

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $VenvPython -m pip install @pipProxyArgs -r $RequirementsFile
Assert-LastExit "Failed to install dependencies"
Write-Host "  Dependencies installed" -ForegroundColor Green

# Set desktop shortcut flag for MSI build if requested
if ($Shortcut) {
    if (-not $Msi) {
        Write-Host "  WARNING: -Shortcut only applies when building MSI (-Msi)." -ForegroundColor Yellow
    }
    $env:GEOLABELLER_MSI_SHORTCUT = "1"
    Write-Host "MSI option: Desktop shortcut enabled" -ForegroundColor Cyan
}

# Change to build directory
Push-Location $ScriptDir

try {
    if ($Msi) {
        if ($Sign) {
            # Build the freeze directory first, sign the inner exe, then package
            # it into the MSI without rebuilding (so the shipped exe is signed).
            Write-Host "Building executable (for signing)..." -ForegroundColor Yellow
            & $VenvPython setup.py build_exe
            Assert-LastExit "build_exe failed"

            $exe = Get-ChildItem -Path (Join-Path $ScriptDir "build") -Recurse -Filter "GeoLabeller.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $exe) { throw "Built GeoLabeller.exe not found for signing." }
            Invoke-SignFile $SignTool $exe.FullName

            Write-Host "Building MSI installer..." -ForegroundColor Yellow
            & $VenvPython setup.py bdist_msi --skip-build
            Assert-LastExit "bdist_msi failed"
        } else {
            Write-Host "Building MSI installer..." -ForegroundColor Yellow
            & $VenvPython setup.py bdist_msi
            Assert-LastExit "bdist_msi failed"
        }
    } else {
        Write-Host "Building executable..." -ForegroundColor Yellow
        & $VenvPython setup.py build
        Assert-LastExit "build failed"

        if ($Sign) {
            $exe = Get-ChildItem -Path (Join-Path $ScriptDir "build") -Recurse -Filter "GeoLabeller.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($exe) { Invoke-SignFile $SignTool $exe.FullName }
        }
    }

    Write-Host ""
    Write-Host "Build completed successfully!" -ForegroundColor Green

    # Find executable output directory
    $outputDir = Get-ChildItem -Path (Join-Path $ScriptDir "build") -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "exe.*" } | Select-Object -First 1
    if ($outputDir) {
        Write-Host "Output location: $($outputDir.FullName)" -ForegroundColor Cyan
    }

    if ($Msi) {
        $msiFile = Get-ChildItem -Path (Join-Path $ScriptDir "dist") -Filter "*.msi" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($msiFile) {
            if ($Sign) { Invoke-SignFile $SignTool $msiFile.FullName }

            # Emit a SHA-256 checksum next to the MSI for distribution.
            $hash = Get-FileHash -Algorithm SHA256 $msiFile.FullName
            "$($hash.Hash)  $($msiFile.Name)" | Out-File -Encoding ascii "$($msiFile.FullName).sha256"

            Write-Host "MSI Installer: $($msiFile.FullName)" -ForegroundColor Cyan
            Write-Host "SHA-256:       $($hash.Hash)" -ForegroundColor Cyan
            if ($Sign) { Write-Host "Signed:        yes" -ForegroundColor Green }
        }
    }
} catch {
    Write-Host "Build failed: $_" -ForegroundColor Red
    exit 1
} finally {
    Pop-Location

    # Clear environment variables we set
    foreach ($name in @("GEOLABELLER_VERSION", "GEOLABELLER_MSI_SHORTCUT", "GEOLABELLER_AUTHOR", "GEOLABELLER_URL")) {
        if (Test-Path "Env:$name") { Remove-Item "Env:$name" }
    }

    # Clean up virtual environment unless -KeepVenv is specified
    if (-not $KeepVenv -and (Test-Path $VenvDir)) {
        Write-Host "Cleaning up virtual environment..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $VenvDir
        Write-Host "  Done" -ForegroundColor Green
    }
}
