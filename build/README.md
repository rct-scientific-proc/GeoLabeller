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
```

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
