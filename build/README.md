# Building GeoLabeller

This directory contains build scripts for creating standalone executables using cx_Freeze.

## Prerequisites

1. **Python 3.10+** with pip installed
2. **cx_Freeze** (will be installed automatically by build scripts if missing)
3. All project dependencies installed (see main `environment.yml`)

## Windows

### Build Executable

```powershell
# From the build directory
.\build_windows.ps1

# Or with clean build
.\build_windows.ps1 -Clean
```

### Behind a corporate proxy

`pip` needs the proxy to fetch dependencies. The script **auto-detects** it from
(in order) the `HTTPS_PROXY`/`HTTP_PROXY` environment variables, the Windows
Internet Options proxy (WinINET registry), then the WinHTTP proxy — and passes
`--proxy` to pip. It prints the proxy it will use. Override or disable it:

```powershell
# Force a specific proxy
.\build_windows.ps1 -Proxy "http://proxy.corp:8080"

# Skip proxy detection (direct connection)
.\build_windows.ps1 -NoProxy
```

### Build MSI Installer

```powershell
.\build_windows.ps1 -Msi

# Build MSI with a Desktop shortcut (a Start Menu shortcut is always added)
.\build_windows.ps1 -Msi -Shortcut

# Set the version explicitly (otherwise auto-detected from a VERSION file or git tag)
.\build_windows.ps1 -Msi -Version 1.2.3

# Set the publisher / about link shown in Add/Remove Programs
.\build_windows.ps1 -Msi -Author "Your Name" -Url "https://example.com/geolabeller"
```

The MSI is a **per-user** install: it installs into
`%LocalAppData%\Programs\GeoLabeller` for the current user and requires **no
administrator elevation**. It adds a (per-user) Start Menu shortcut (Desktop
optional via `-Shortcut`), and records the publisher, version and icon in Add/
Remove Programs. Keep incrementing the version between releases so a new MSI
cleanly upgrades a previous install (the upgrade GUID in `setup.py` must never
change).

> Because it installs per-user, each Windows user who wants GeoLabeller runs the
> MSI themselves; it is not installed once for the whole machine. To deploy it
> machine-wide instead (into Program Files, for all users), an administrator can
> run `msiexec /i GeoLabeller-<ver>.msi ALLUSERS=1` — that path does require
> admin rights.

### Code Signing (recommended for distribution)

Unsigned installers trigger SmartScreen / "unknown publisher" warnings. Sign the
executable and the MSI with an Authenticode certificate:

```powershell
# Using a certificate already in a Windows certificate store (by thumbprint)
.\build_windows.ps1 -Msi -Sign -CertThumbprint "ABC123..."

# Using a .pfx file
.\build_windows.ps1 -Msi -Sign -CertPath "C:\certs\code.pfx" -CertPassword "***"
```

Requires `signtool.exe` (Windows SDK) on PATH or installed under the Windows Kits
directory. A `.sha256` checksum file is written next to the produced `.msi`.

### Manual Build

```powershell
cd build
python setup.py build
```

The output will be in `build/exe.win-amd64-3.x/` (or similar).

## Linux

### Build Executable

```bash
# Make script executable (first time only)
chmod +x build_linux.sh

# Build
./build_linux.sh

# Or with clean build
./build_linux.sh --clean
```

### Manual Build

```bash
cd build
python3 setup.py build
```

The output will be in `build/exe.linux-x86_64-3.x/` (or similar).

## Output

After building, you'll find:

- **Windows**: `build/exe.win-amd64-3.x/GeoLabeller.exe`
- **Linux**: `build/exe.linux-x86_64-3.x/GeoLabeller`

The entire output directory contains all dependencies and should be distributed together.

## Customization

Edit `setup.py` to:

- Add an application icon (set `icon` parameter in `Executable`)
- Include additional data files (add to `include_files` list)
- Modify included/excluded packages
- Change version number and metadata

## Troubleshooting

### Missing DLLs (Windows)

If the built executable complains about missing DLLs, you may need to:

1. Install the Microsoft Visual C++ Redistributable
2. Add the missing DLLs to `include_files` in setup.py

### Missing Python Modules

If you get import errors at runtime, add the missing module to the `packages` list in setup.py.

### Large Build Size

To reduce build size:

1. Add unused packages to the `excludes` list
2. Use a virtual environment with only required packages
