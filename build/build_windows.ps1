# GeoLabeller Windows Build Script
# Builds the application using cx_Freeze in a temporary virtual environment.
# Can optionally produce a signed .msi installer.

param(
    [switch]$Msi,             # Build MSI installer instead of just executable
    [switch]$Wix,             # Package the MSI with WiX (install-time options) instead of bdist_msi
    [switch]$Shortcut,        # Also install a Desktop shortcut (ignored with -Wix: the user chooses)
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

# Locate wix.exe, installing the toolset if this machine has not got it.
#
# The version is pinned deliberately: WiX v6 and later require accepting the
# Open Source Maintenance Fee EULA, which is a licensing decision rather than a
# build detail, so upgrading past v5 must be someone's choice and not something
# that happens because a floating version moved on.
function Initialize-WixToolset {
    $wixVersion = "5.0.2"
    $wixExe = Join-Path $env:USERPROFILE ".dotnet\tools\wix.exe"
    if (-not (Test-Path $wixExe)) {
        $onPath = Get-Command wix -ErrorAction SilentlyContinue
        if ($onPath) {
            $wixExe = $onPath.Source
        } else {
            Write-Host "Installing WiX $wixVersion..." -ForegroundColor Yellow
            # Out-Host, not bare invocation: anything a command writes to the
            # output stream inside a function becomes part of that function's
            # return value. dotnet's "Tool 'wix' was successfully installed"
            # chatter would be returned alongside the path, and the caller
            # would try to run the whole lot as the name of an executable.
            & dotnet tool install --global wix --version $wixVersion | Out-Host
            Assert-LastExit "Could not install WiX (is the .NET SDK installed?)"
        }
    }

    $installed = (& $wixExe --version 2>&1) -join ""
    if ($installed -notmatch '^5\.') {
        Write-Host "  WARNING: WiX $installed is not the pinned 5.x; the UI extension may not match." -ForegroundColor Yellow
    }

    # The UI extension carries the dialogs that offer per-user/per-machine and
    # the shortcut choices, and its version has to match the toolset's.
    $extensions = (& $wixExe extension list -g 2>&1) -join "`n"
    if ($extensions -notmatch "WixToolset\.UI\.wixext") {
        Write-Host "Adding the WiX UI extension..." -ForegroundColor Yellow
        & $wixExe extension add -g "WixToolset.UI.wixext/$wixVersion" | Out-Host
        Assert-LastExit "Could not add the WiX UI extension"
    }
    return $wixExe
}

# Render LICENSE as the RTF the installer's licence page needs.
#
# Generated rather than kept as a second copy, so the licence someone agrees to
# during setup cannot drift from the one in the repository. Without it WixUI
# falls back to its own placeholder, which is Lorem ipsum.
function New-LicenseRtf([string]$SourcePath, [string]$OutPath) {
    if (-not (Test-Path $SourcePath)) {
        throw "No LICENSE at $SourcePath - the installer needs licence text."
    }
    $escaped = (Get-Content $SourcePath -Raw) `
        -replace '\\', '\\' -replace '\{', '\{' -replace '\}', '\}'
    $body = ($escaped -split "`r?`n" | ForEach-Object { "$_\par" }) -join "`r`n"
    $rtf = "{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fswiss\fcharset0 Tahoma;}}`r`n" +
           "\viewkind4\uc1\pard\f0\fs18 $body`r`n}"
    Set-Content -Path $OutPath -Value $rtf -Encoding ascii
    return $OutPath
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

# Catch a module the freeze would leave out before spending ten minutes
# building an executable that cannot start. See check_frozen_imports.py.
Write-Host "Checking frozen imports..." -ForegroundColor Yellow
& $Python (Join-Path $ScriptDir "check_frozen_imports.py")
if ($LASTEXITCODE -ne 0) { exit 1 }

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
    } elseif ($Wix) {
        Write-Host "  WARNING: -Shortcut is ignored with -Wix; shortcuts are chosen during install." -ForegroundColor Yellow
    }
    $env:GEOLABELLER_MSI_SHORTCUT = "1"
    Write-Host "MSI option: Desktop shortcut enabled" -ForegroundColor Cyan
}

# The WiX authoring stamps the version into the package and names the output
# after it, so it cannot be inferred later the way bdist_msi manages.
if ($Wix) {
    if (-not $Msi) {
        Write-Host "  ERROR: -Wix builds the installer, so it needs -Msi as well." -ForegroundColor Red
        exit 1
    }
    if (-not $Version) {
        Write-Host "  ERROR: -Wix needs a version: pass -Version X.Y.Z or add a VERSION file." -ForegroundColor Red
        exit 1
    }
    if (-not $Author) { $Author = "RCT Scientific Processing" }
    if (-not $Url) { $Url = "https://github.com/rct-scientific-proc/GeoLabeller" }
}

# Change to build directory
Push-Location $ScriptDir

try {
    if ($Msi -and $Wix) {
        # WiX packages a directory that has already been frozen, so build_exe
        # runs on its own first. Signing happens in between, which means the
        # executable inside the MSI is the signed one.
        Write-Host "Building executable..." -ForegroundColor Yellow
        & $VenvPython setup.py build_exe
        Assert-LastExit "build_exe failed"

        $payload = Get-ChildItem -Path (Join-Path $ScriptDir "build") -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "exe.*" } | Select-Object -First 1
        if (-not $payload) { throw "cx_Freeze produced no exe.* directory to package." }

        if ($Sign) {
            $exe = Join-Path $payload.FullName "GeoLabeller.exe"
            if (-not (Test-Path $exe)) { throw "GeoLabeller.exe not found in $($payload.FullName)." }
            Invoke-SignFile $SignTool $exe
        }

        Write-Host "Building MSI installer (WiX)..." -ForegroundColor Yellow
        $wixExe = Initialize-WixToolset
        $licenseRtf = New-LicenseRtf `
            (Join-Path $ScriptDir "..\LICENSE") `
            (Join-Path $payload.FullName "..\License.rtf")

        $distDir = Join-Path $ScriptDir "dist"
        New-Item -ItemType Directory -Force -Path $distDir | Out-Null
        $msiPath = Join-Path $distDir "GeoLabeller-$Version-win64.msi"

        # Each -d is quoted whole: the publisher name contains spaces, and an
        # unquoted "Manufacturer=RCT Scientific Processing" arrives as three
        # arguments and fails with a preprocessor error a long way from here.
        & $wixExe build -arch x64 -ext WixToolset.UI.wixext `
            -d "Version=$Version" `
            -d "PayloadDir=$($payload.FullName)" `
            -d "Manufacturer=$Author" `
            -d "AboutUrl=$Url" `
            -d "IconFile=$(Join-Path $ScriptDir 'geolabel_icon.ico')" `
            -d "LicenseRtf=$licenseRtf" `
            -o $msiPath `
            (Join-Path $ScriptDir "wix\GeoLabeller.wxs")
        Assert-LastExit "wix build failed"

        # `wix build` does not run ICE validation, and a package can build
        # cleanly while still being wrong in ways that only surface when
        # someone installs it. ICE57 is suppressed for the reason recorded
        # against the shortcut components in GeoLabeller.wxs.
        Write-Host "Validating the MSI..." -ForegroundColor Yellow
        & $wixExe msi validate -sice ICE57 $msiPath
        Assert-LastExit "the built MSI failed validation"
    } elseif ($Msi) {
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
