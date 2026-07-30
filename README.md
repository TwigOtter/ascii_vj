# ascii_vj
Turn videos into ascii text

## Build portable EXE

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
