# ascii_vj
Turn videos into ascii text

## Build portable EXE

Dependencies needed to run `build_portable_exe.ps1`:

- PowerShell (`pwsh`)
- Python 3
- `pip`
- Internet access to install Python build packages

The build script installs these Python packages automatically:

- `pyinstaller`
- `imageio-ffmpeg`
- `opencv-python`
- `pillow`
- `numpy`

From the repository root, run:

```powershell
./build_portable_exe.ps1 -Clean
```

Output:

- `dist/TwigsAsciiVJConverter.exe`

Optional parameters:

- `-PythonExe "C:\\Path\\To\\python.exe"` to force a specific Python interpreter
- `-AppName "MyAsciiBuild"` to change the output EXE name
- `-Clean` to remove previous `build`, `dist`, and generated `.spec` files before rebuilding
