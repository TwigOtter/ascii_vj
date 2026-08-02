# ascii_vj

Turns green-screen dance footage into chroma-keyed, beat-synced ASCII art -- 
built as a silly side project for VRChat DJ events, but hey, it's pretty cool!

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

This produces `dist/TwigsAsciiVJConverter.exe`. See the script's parameters
(`-PythonExe`, `-AppName`) if you need to point it at a specific Python or
rename the build.

### 2. CLI (`Converter/ascii_vj.py`)

More control, easier to script/batch. Basic example:

```bash
python Converter/ascii_vj.py input.mp4 output.mp4 \
  --cols 140 --key-hex 00ff00 \
  --bg-mode whitespace \
  --fg-color-mode beat-cycle --fg-palette ff0050,00ffff,ffff00 \
  --bg-color-mode static --bg-color 0f0f14 \
  --bpm 87
```

Auto-detecting the beat from the video's own audio instead of guessing a
BPM:

```bash
python Converter/ascii_vj.py input.mp4 output.mp4 \
  --cols 140 --auto-beats \
  --fg-color-mode beat-cycle --fg-palette ff0050,00ffff,ffff00
```

Transparent-background output for compositing (must be `.mov`):

```bash
python Converter/ascii_vj.py input.mp4 output_alpha.mov \
  --cols 140 --transparent-bg
```

Then remux the original audio back in separately:

```bash
python Remux/audio_remux_simple.py input.mp4 output.mp4 output_with_audio.mp4
```

## Key options reference

| Flag | What it does |
|---|---|
| `--cols` | Grid width in characters. Higher = more detail, slower to render. 100–160 is a good range. |
| `--charset` | Ramp of characters from darkest to brightest, used for the foreground luminance mapping. |
| `--key-hex` / `--key-tol-h/-s/-v` | Chroma key color and hue/saturation/value tolerances. Loosen `-s`/`-v` if background isn't keying out cleanly. |
| `--bg-mode` | How keyed-out background cells are filled: `whitespace`, `char` (fixed character), `random` (glitchy noise), `solid` (solid color). |
| `--fg-color-mode` / `--bg-color-mode` | `static`, `beat-cycle` (step through a palette on the beat), `beat-random-per-char` (per-character random recolor on the beat — the "overclocked Matrix" look), or `source` (foreground only — use the actual video's colors). |
| `--bpm` | Fixed tempo for beat-synced color changes. |
| `--auto-beats` | Detect real beat timestamps from the input's audio track instead of assuming a fixed BPM. Requires `librosa` + `soundfile`. Tracks tempo drift correctly; a fixed BPM will slowly fall out of sync over a long set. |
| `--brightness-clamp` | Dims randomly-generated colors (0–1). Keeps the random-color modes from being a harsh full-brightness flash. |
| `--transparent-bg` | Output RGBA `.mov` instead of a solid-background `.mp4`, for compositing over other layers. |
| `--seed` | Fix the random seed for reproducible random-mode output. |
| `--max-frames` | Cap frame count, for quick test renders before committing to a full pass. |

Run `python Converter/ascii_vj.py --help` for the full list.

## Requirements

```bash
pip install -r requirements.txt
```

Covers OpenCV, NumPy, Pillow, imageio-ffmpeg (for transparent `.mov` output),
and librosa/soundfile (for `--auto-beats`). A monospace font is
auto-detected per OS (DejaVu Sans Mono on Linux, Menlo/Monaco on macOS,
Consolas/Courier on Windows); pass `--font-path` if none is found.
