# Building GeoLabeller

This directory holds the Windows packaging: cx_Freeze freezes the application,
WiX packages it as an MSI installer. Windows is the only build target. Linux
users run the application from a conda or virtual environment (see the
repository README); there is no Linux build script.

## Prerequisites

1. **Python 3.10+** with pip. The release workflow pins 3.12.
2. **The .NET SDK**, for the WiX toolset (installed on first use by the script).
3. Internet access for pip, or a proxy (see below).

The build creates its own virtual environment from `requirements.txt` and
removes it afterwards, so nothing else needs installing by hand.

## Build the installer

```powershell
# From the repository root. Installs WiX 5 on first use (needs the .NET SDK).
.\build\build_windows.ps1 -Msi -Wix -Version 1.2.3

# Set the publisher / about link shown in Add/Remove Programs
.\build\build_windows.ps1 -Msi -Wix -Version 1.2.3 -Author "Your Name" -Url "https://example.com/geolabeller"

# Start from nothing (clears build/build, build/dist and the build venv)
.\build\build_windows.ps1 -Msi -Wix -Version 1.2.3 -Clean
```

The version is read from the `VERSION` file at the repository root when
`-Version` is not given. Keep incrementing it between releases so a new MSI
cleanly upgrades a previous install. **The upgrade GUID must never change** -
it is what tells Windows Installer that a package replaces an existing install
rather than being a different product. The same GUID appears in `setup.py` and
in `wix/GeoLabeller.wxs`.

The output is `build/dist/GeoLabeller-<version>-win64.msi` with a `.sha256`
checksum beside it. This is what `.github/workflows/release.yml` runs on a tag.

### Behind a corporate proxy

`pip` needs the proxy to fetch dependencies. The script **auto-detects** it from
(in order) the `HTTPS_PROXY`/`HTTP_PROXY` environment variables, the Windows
Internet Options proxy (WinINET registry), then the WinHTTP proxy - and passes
`--proxy` to pip. It prints the proxy it will use. Override or disable it:

```powershell
# Force a specific proxy
.\build\build_windows.ps1 -Msi -Wix -Proxy "http://proxy.corp:8080"

# Skip proxy detection (direct connection)
.\build\build_windows.ps1 -Msi -Wix -NoProxy
```

### What the installer asks

The dialog flow is the package's own (`wix/GeoLabeller.wxs`), built from the
WiX UI extension's standard pages:

1. **Welcome**, or **an existing installation was found** when an older
   version is installed. That page names the installed version and explains
   that it is replaced; the only options are to go on or cancel. There is no
   side-by-side install: two copies would share the upgrade GUID, which
   Windows Installer reads as one product.
2. **Licence.** `LICENSE` from the repository root, converted to RTF at build
   time so there is only one copy of it. Shown once; it has to be accepted to
   go on.
3. **Scope.** Install just for you, or for all users of this machine. Just-me
   is the default and needs no administrator prompt; a standard user is not
   offered the choice. Everyone elevates and installs into Program Files.
4. **Folder.** The default for the chosen scope, or **Change...** to browse.
   Per-user defaults to `%LocalAppData%\Apps\GeoLabeller`, per-machine to
   `C:\Program Files\GeoLabeller`. On an upgrade the default is wherever the
   previous version was installed, so a folder chosen once stays chosen.
5. **Shortcuts.** Start Menu and Desktop are separate features with
   tick-boxes, both on by default. Then **Install**.

Before 2.3.1 the installer used the stock `WixUI_Advanced` set, which had
two faults users noticed: an upgrade showed the licence twice (the upgrade
notice was sequenced as a second dialog, and Windows Installer ran the stock
licence page again after it returned), and a per-user install had no folder
page at all. The package sequences no dialog of its own now; every page is
reached from the Welcome page's chain, which the sequence runs once.

The version and the install folder are recorded under
`HKCU`/`HKLM\Software\<publisher>\GeoLabeller` for the next upgrade to read.
Installs made by the old cx_Freeze packager never wrote them, so upgrading
from 1.3.0 or earlier shows the wording that does not name a version and
offers the stock default folder.

### What lands in the install directory

Beside `GeoLabeller.exe`, cx_Freeze copies:

- `proj_data/` and `gdal_data/` - PROJ and GDAL support files, so coordinate
  transforms work without a system GDAL install.
- `VERSION` - read by `app/version.py` for the window title.
- `GeoLabeller-ICD.pdf` - the built document from `docs/`, opened by
  **Help > ICD**. `include_files` flattens it into the install root rather
  than a `docs/` subfolder, which is why `app/resources.py` looks for it
  beside the executable.

Each is copied only when present, so a build never fails over a missing one -
the application degrades instead (no version in the title, Help > ICD reports
where it looked).

To deploy machine-wide without the dialogs, an administrator can run
`msiexec /i GeoLabeller-<ver>.msi ALLUSERS=1 /qn`; a silent per-user install
is `msiexec /i GeoLabeller-<ver>.msi /qn`. Add `APPLICATIONFOLDER=<path>` to
choose the folder.

### Code signing (recommended for distribution)

Unsigned installers trigger SmartScreen / "unknown publisher" warnings. Sign the
executable and the MSI with an Authenticode certificate:

```powershell
# Using a certificate already in a Windows certificate store (by thumbprint)
.\build\build_windows.ps1 -Msi -Wix -Sign -CertThumbprint "ABC123..."

# Using a .pfx file
.\build\build_windows.ps1 -Msi -Wix -Sign -CertPath "C:\certs\code.pfx" -CertPassword "***"
```

Requires `signtool.exe` (Windows SDK) on PATH or installed under the Windows Kits
directory. The executable is signed before it is packaged, then the MSI.

### The cx_Freeze packager

`.\build\build_windows.ps1 -Msi` without `-Wix` builds an installer with
cx_Freeze's own `bdist_msi`, which needs no toolchain beyond Python but bakes
its choices in at build time (per-user only, `-Shortcut` for a Desktop
shortcut). It shares the upgrade GUID, so an install made by either packager
upgrades to one made by the other. Releases do not use it.

### Just the frozen application

```powershell
.\build\build_windows.ps1
```

produces `build/build/exe.win-amd64-3.x/GeoLabeller.exe` with everything it
needs beside it; the whole directory is what runs. `python setup.py build`
from this directory does the same without the virtual environment.

## Troubleshooting

### Missing DLLs

If the built executable complains about missing DLLs, install the Microsoft
Visual C++ Redistributable, or add the missing DLLs to `include_files` in
`setup.py`.

### Missing Python modules

If you get import errors at runtime, add the missing module to the `packages`
list in `setup.py`. `check_frozen_imports.py` runs before every build and fails
it if the application imports anything on the `excludes` list.

### A fat installer

The MSI should be about 67 MB. cx_Freeze can nondeterministically ship the
whole Qt wheel (~24 MB more); `setup.py` pins the behaviour that avoids it and
the build script fails if the canaries of a fat payload appear. See the
comments in both.
