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

The MSI installs into the 64-bit `Program Files\GeoLabeller`, adds a Start Menu
shortcut (Desktop optional via `-Shortcut`), and records the publisher, version
and icon in Add/Remove Programs. Keep incrementing the version between releases
so a new MSI cleanly upgrades a previous install (the upgrade GUID in `setup.py`
must never change).

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
