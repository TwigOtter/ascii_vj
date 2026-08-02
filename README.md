# ascii_vj

Turns green-screen dance footage into chroma-keyed, beat-synced ASCII art -- 
built as a silly side project for VRChat DJ events, but hey, it's pretty cool!

## What this is for

The intended pipeline: record yourself dancing in front of a green screen
(e.g. a VRChat avatar recording), then run that footage through this tool to
get an ASCII-art cutout of just the dancer, with colors that shift and pulse
on the beat.

Because the background is chroma-keyed out first, you get a clean character-art
silhouette of just the subject — no ASCII-ifying a background you don't want.
Colors can be static, cycle through a palette on the beat, go fully random per
character for a glitchy/chaotic look, or pull straight from the source
footage's actual colors.

## Two ways to use it

### 1. GUI (`ascii_vj_ui.py`)

_Shoutout to Dralzin for this UI design :3_

The easiest path if you don't want to touch a terminal. A Tkinter app with
three tabs:

- **Job** — pick input/output files, and optionally enable "remux original
  audio after conversion" to get a second output file with the DJ track
  audio muxed back in (the ASCII conversion itself produces a silent video).
- **Style** — charset, colors, palettes, beat/BPM settings, font.
- **Advanced** — chroma-key tuning, brightness clamp (for photosensitivity
  safety on the random-color modes), random seed, frame limit for quick tests.

Run it directly:

```bash
python ascii_vj_ui.py
```

Or build a portable Windows `.exe` that doesn't require Python installed on
the target machine:

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
