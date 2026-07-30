#!/usr/bin/env python3
"""
ascii_vj.py — Chroma-keyed ASCII-art video converter with BPM-synced color effects.

Built for: green-screen dance footage -> ASCII cutout, recolored on the beat.

Pipeline:
  1. Read frames (OpenCV).
  2. Chroma-key the background (HSV threshold) -> boolean "is background" mask.
  3. Downsample to a character grid; map luminance -> ASCII ramp char (foreground),
     and separately choose background fill (whitespace / static char / random-per-frame).
  4. Color each cell (foreground & background independently), optionally re-rolled
     on every musical beat computed from BPM.
  5. Composite the whole frame in one vectorized numpy op (pre-rendered glyph
     bitmaps, gathered via fancy indexing) -- NOT a per-cell draw call, so this
     stays fast even at high frame counts.
  6. Write frames out via OpenCV VideoWriter (mux audio yourself in ffmpeg after,
     see bottom of file for a one-liner).

Usage example:
  python3 ascii_vj.py input.mp4 output.mp4 \\
      --cols 140 --key-hex 00ff00 \\
      --bg-mode whitespace \\
      --fg-color-mode beat-cycle --fg-palette ff0050,00ffff,ffff00 \\
      --bg-color-mode static --bg-color 10,10,15 \\
      --bpm 87
"""
import argparse
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def find_default_monospace_font():
    """Look for a common monospace TTF across Linux/macOS/Windows.
    Returns a path, or None if nothing found (PIL default bitmap font is used then)."""
    candidates = [
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        # macOS
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        # Windows
        "C:\\Windows\\Fonts\\consolab.ttf",
        "C:\\Windows\\Fonts\\consola.ttf",
        "C:\\Windows\\Fonts\\cour.ttf",
        "C:\\Windows\\Fonts\\lucon.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

@dataclass
class Config:
    input_path: str
    output_path: str
    cols: int = 120
    charset: str = " .:-=+*#%@"           # dark -> light ramp for foreground luminance
    font_path: str = None                 # None = auto-detect a system monospace font
    cell_w: int = 10                      # px width of one rendered character cell
    cell_h: int = 18                      # px height of one rendered character cell

    key_hex: str = "00ff00"
    key_tol_h: int = 25                   # hue tolerance, OpenCV 0-180 scale
    key_tol_s: int = 100                  # min saturation to count as "green screen"
    key_tol_v: int = 70                   # min value to count as "green screen"

    bg_mode: str = "whitespace"           # whitespace | char | random | solid
    bg_char: str = "."

    bg_color_mode: str = "static"         # static | beat-cycle | beat-random-per-char
    fg_color_mode: str = "static"         # static | beat-cycle | beat-random-per-char | source
    bg_color: tuple = (15, 15, 20)
    fg_color: tuple = (255, 255, 255)
    bg_palette: list = field(default_factory=lambda: [(255, 0, 80), (0, 255, 255), (255, 255, 0)])
    fg_palette: list = field(default_factory=lambda: [(255, 0, 80), (0, 255, 255), (255, 255, 0)])

    bpm: float = None                     # None = no beat sync, colors stay static
    brightness_clamp: float = 1.0         # 0-1, scales random colors down (photosensitivity safety)
    seed: int = None

    out_w: int = None                     # output pixel size; default = cols*cell_w
    out_h: int = None
    transparent_bg: bool = False          # write keyed background as transparent alpha


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------
# Glyph bitmap pre-rendering
# --------------------------------------------------------------------------

def build_glyph_table(charset, font_path, cell_w, cell_h):
    """Render each character once as a grayscale alpha bitmap (cell_h, cell_w),
    0=transparent, 255=fully opaque glyph. Returns dict char -> np.uint8 array,
    plus a stacked array + char->index map for vectorized lookup."""
    font_size = int(cell_h * 0.92)
    if font_path is None:
        font_path = find_default_monospace_font()
    if font_path is None:
        raise RuntimeError(
            "Could not auto-detect a monospace font on this system. "
            "Pass --font-path pointing at a .ttf/.ttc file (e.g. C:\\Windows\\Fonts\\consola.ttf "
            "on Windows, or any monospace font you have installed)."
        )
    font = ImageFont.truetype(font_path, font_size)

    chars = list(dict.fromkeys(charset))  # unique, preserve order
    templates = np.zeros((len(chars), cell_h, cell_w), dtype=np.float32)

    for i, ch in enumerate(chars):
        img = Image.new("L", (cell_w, cell_h), 0)
        draw = ImageDraw.Draw(img)
        # center the glyph in its cell
        bbox = draw.textbbox((0, 0), ch, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (cell_w - tw) / 2 - bbox[0]
        y = (cell_h - th) / 2 - bbox[1]
        draw.text((x, y), ch, fill=255, font=font)
        templates[i] = np.asarray(img, dtype=np.float32)

    char_to_idx = {ch: i for i, ch in enumerate(chars)}
    return templates, char_to_idx


# --------------------------------------------------------------------------
# Chroma key
# --------------------------------------------------------------------------

def chroma_key_mask(frame_bgr, key_hex, tol_h, tol_s, tol_v):
    """Returns boolean mask, True where pixel IS background (should be keyed out)."""
    key_rgb = hex_to_rgb(key_hex)
    key_bgr = np.uint8([[list(key_rgb)[::-1]]])
    key_hsv = cv2.cvtColor(key_bgr, cv2.COLOR_BGR2HSV)[0, 0]

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)

    # hue wraps at 180 in OpenCV
    dh = np.minimum(np.abs(h - int(key_hsv[0])), 180 - np.abs(h - int(key_hsv[0])))
    mask = (dh <= tol_h) & (s >= tol_s) & (v >= tol_v)
    return mask


# --------------------------------------------------------------------------
# Beat scheduling
# --------------------------------------------------------------------------

class BeatClock:
    """Tracks which frames land on a beat given BPM + fps."""

    def __init__(self, bpm, fps):
        self.frames_per_beat = None if not bpm else (60.0 / bpm) * fps
        self.last_beat_num = -1

    def is_beat(self, frame_idx):
        if self.frames_per_beat is None:
            return frame_idx == 0  # only "beat" is frame 0 -> colors set once
        # fires once, on the first frame that reaches each new beat number
        # (must be called once per frame, in increasing order, since it's stateful)
        beat_num = int(frame_idx / self.frames_per_beat)
        if beat_num != self.last_beat_num:
            self.last_beat_num = beat_num
            return True
        return False


# --------------------------------------------------------------------------
# Color state (re-rolled on beats depending on mode)
# --------------------------------------------------------------------------

class ColorState:
    def __init__(self, mode, static_color, palette, rows, cols, rng, brightness_clamp):
        self.mode = mode
        self.static_color = np.array(static_color, dtype=np.float32)
        self.palette = [np.array(c, dtype=np.float32) for c in palette] if palette else [self.static_color]
        self.rows, self.cols = rows, cols
        self.rng = rng
        self.brightness_clamp = brightness_clamp
        self.palette_idx = 0
        self.current = None
        self.roll()  # initialize

    def roll(self):
        """Called on every beat (or once, for static)."""
        if self.mode == "static":
            self.current = np.broadcast_to(self.static_color, (self.rows, self.cols, 3)).copy()
        elif self.mode == "beat-cycle":
            color = self.palette[self.palette_idx % len(self.palette)]
            self.palette_idx += 1
            self.current = np.broadcast_to(color, (self.rows, self.cols, 3)).copy()
        elif self.mode == "beat-random-per-char":
            rand = self.rng.random((self.rows, self.cols, 3)).astype(np.float32) * 255.0
            rand *= self.brightness_clamp
            self.current = rand
        else:
            raise ValueError(f"unknown color mode: {self.mode}")


# --------------------------------------------------------------------------
# Core per-frame processing
# --------------------------------------------------------------------------

def frame_to_grid(frame_bgr, cols, cell_aspect):
    """Downsample frame to (rows, cols) grayscale + keep small color/mask ref."""
    h, w = frame_bgr.shape[:2]
    rows = max(1, int(cols * (h / w) * cell_aspect))
    small = cv2.resize(frame_bgr, (cols, rows), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return small, gray, rows


def composite_frame(char_idx_grid, color_grid, templates, transparent_bg=False, fg_mask=None,
                    solid_bg=False, bg_mask=None):
    """Vectorized: gather glyph alpha per cell, apply per-cell color,
    and reshape into full output canvas. No per-cell Python loop."""
    rows, cols = char_idx_grid.shape
    ch, cw = templates.shape[1], templates.shape[2]

    # gather: (rows, cols, ch, cw)
    alpha_cells = templates[char_idx_grid]  # fancy indexing
    if fg_mask is not None:
        alpha_cells = alpha_cells * fg_mask[:, :, None, None].astype(np.float32)

    alpha = alpha_cells.transpose(0, 2, 1, 3).reshape(rows * ch, cols * cw)  # (H, W)

    color_full = np.broadcast_to(
        color_grid[:, None, :, None, :], (rows, ch, cols, cw, 3)
    ).reshape(rows * ch, cols * cw, 3)

    if transparent_bg:
        rgb = color_full.clip(0, 255).astype(np.uint8)
        a = alpha.clip(0, 255).astype(np.uint8)
        return np.dstack((rgb, a))

    alpha_n = (alpha / 255.0)[..., None]
    canvas = (alpha_n * color_full).clip(0, 255).astype(np.uint8)

    if solid_bg and bg_mask is not None:
        bg_mask_full = np.broadcast_to(
            bg_mask[:, None, :, None, None], (rows, ch, cols, cw, 1)
        ).reshape(rows * ch, cols * cw, 1)
        bg_color_full = color_full.clip(0, 255).astype(np.uint8)
        canvas = np.where(bg_mask_full, bg_color_full, canvas)

    return canvas


class ConversionCancelled(Exception):
    pass


class TransparentMovWriter:
    """Writes RGBA frames to a .mov using ffmpeg qtrle codec (supports alpha)."""

    def __init__(self, output_path, fps, width, height):
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:
            raise RuntimeError(
                "Transparent output requires imageio-ffmpeg. Install with: pip install imageio-ffmpeg"
            ) from e

        cmd = [
            ffmpeg_exe,
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{width}x{height}",
            "-r", str(float(fps)),
            "-i", "-",
            "-an",
            "-c:v", "qtrle",
            "-pix_fmt", "argb",
            output_path,
        ]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, frame_rgba):
        if self.proc.stdin is None:
            raise RuntimeError("ffmpeg writer stdin is not available")
        self.proc.stdin.write(frame_rgba.tobytes())

    def release(self):
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed while writing transparent output (exit code {rc})")


# --------------------------------------------------------------------------
# Main conversion
# --------------------------------------------------------------------------

def convert(cfg: Config, max_frames=None, progress_every=30, cancel_event=None):
    np_rng = np.random.default_rng(cfg.seed)

    if cfg.transparent_bg and not cfg.output_path.lower().endswith(".mov"):
        raise RuntimeError("Transparent output requires a .mov output path (e.g. out/output_alpha.mov)")

    cap = cv2.VideoCapture(cfg.input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open input video: {cfg.input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    charset_full = cfg.charset + (cfg.bg_char if cfg.bg_mode == "char" else "")
    templates, char_to_idx = build_glyph_table(charset_full, cfg.font_path, cfg.cell_w, cfg.cell_h)

    # cell aspect ratio correction: character cells are taller than wide,
    # so we need fewer rows than a naive w/h ratio would give -> use cell_w/cell_h
    cell_aspect = cfg.cell_w / cfg.cell_h

    out_w = cfg.out_w or cfg.cols * cfg.cell_w
    writer = None  # created after first frame once we know rows

    beat_clock = BeatClock(cfg.bpm, fps)
    ramp = cfg.charset
    ramp_idx_lut = np.array([char_to_idx[c] for c in ramp])

    bg_color_state = None
    fg_color_state = None
    cancelled = False

    frame_idx = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break

        ret, frame = cap.read()
        if not ret:
            break
        if max_frames and frame_idx >= max_frames:
            break

        small, gray, rows = frame_to_grid(frame, cfg.cols, cell_aspect)

        if bg_color_state is None:
            bg_color_state = ColorState(cfg.bg_color_mode, cfg.bg_color, cfg.bg_palette,
                                         rows, cfg.cols, np_rng, cfg.brightness_clamp)
            if cfg.fg_color_mode != "source":
                fg_color_state = ColorState(cfg.fg_color_mode, cfg.fg_color, cfg.fg_palette,
                                             rows, cfg.cols, np_rng, cfg.brightness_clamp)

        if beat_clock.is_beat(frame_idx) and frame_idx != 0:
            bg_color_state.roll()
            if fg_color_state is not None:
                fg_color_state.roll()

        # chroma key on the small frame (cheap + matches grid resolution)
        is_bg = chroma_key_mask(small, cfg.key_hex, cfg.key_tol_h, cfg.key_tol_s, cfg.key_tol_v)

        # foreground char: luminance -> ramp index
        lum_idx = np.clip((gray.astype(np.float32) / 255.0 * (len(ramp) - 1)).round().astype(int),
                           0, len(ramp) - 1)
        char_idx_grid = ramp_idx_lut[lum_idx]

        # background fill
        if cfg.bg_mode in ("whitespace", "solid"):
            bg_idx = char_to_idx.get(" ", char_to_idx[ramp[0]])
            char_idx_grid = np.where(is_bg, bg_idx, char_idx_grid)
        elif cfg.bg_mode == "char":
            bg_idx = char_to_idx[cfg.bg_char]
            char_idx_grid = np.where(is_bg, bg_idx, char_idx_grid)
        elif cfg.bg_mode == "random":
            rand_idx = np_rng.integers(0, len(charset_full), size=char_idx_grid.shape)
            char_idx_grid = np.where(is_bg, rand_idx, char_idx_grid)

        if cfg.fg_color_mode == "source":
            fg_current = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32)
        else:
            fg_current = fg_color_state.current

        color_grid = np.where(is_bg[..., None], bg_color_state.current, fg_current)

        canvas = composite_frame(
            char_idx_grid,
            color_grid,
            templates,
            transparent_bg=cfg.transparent_bg,
            fg_mask=((~is_bg) if cfg.transparent_bg else None),
            solid_bg=(cfg.bg_mode == "solid"),
            bg_mask=is_bg,
        )

        if writer is None:
            h_out, w_out = canvas.shape[:2]
            if cfg.transparent_bg:
                writer = TransparentMovWriter(cfg.output_path, fps, w_out, h_out)
            else:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(cfg.output_path, fourcc, fps, (w_out, h_out))

        if cfg.transparent_bg:
            writer.write(canvas)
        else:
            writer.write(cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))

        frame_idx += 1
        if frame_idx % progress_every == 0:
            print(f"  frame {frame_idx}/{total_frames}", file=sys.stderr)

    cap.release()
    if writer:
        writer.release()
    if cancelled:
        raise ConversionCancelled("Conversion cancelled by user.")
    print(f"Done: {frame_idx} frames -> {cfg.output_path}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_color_list(s):
    return [hex_to_rgb(c) for c in s.split(",")]


def build_argparser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--cols", type=int, default=120)
    p.add_argument("--charset", default=" .:-=+*#%@")
    p.add_argument("--font-path", default=None,
                    help="Path to a monospace .ttf/.ttc. Auto-detected per-OS if omitted.")
    p.add_argument("--cell-w", type=int, default=10)
    p.add_argument("--cell-h", type=int, default=18)

    p.add_argument("--key-hex", default="00ff00")
    p.add_argument("--key-tol-h", type=int, default=25)
    p.add_argument("--key-tol-s", type=int, default=100)
    p.add_argument("--key-tol-v", type=int, default=70)

    p.add_argument("--bg-mode", choices=["whitespace", "char", "random", "solid"], default="whitespace")
    p.add_argument("--bg-char", default=".")

    p.add_argument("--bg-color-mode", choices=["static", "beat-cycle", "beat-random-per-char"], default="static")
    p.add_argument("--fg-color-mode", choices=["static", "beat-cycle", "beat-random-per-char", "source"], default="static")
    p.add_argument("--bg-color", default="0f0f14")
    p.add_argument("--fg-color", default="ffffff")
    p.add_argument("--bg-palette", default="ff0050,00ffff,ffff00")
    p.add_argument("--fg-palette", default="ff0050,00ffff,ffff00")

    p.add_argument("--bpm", type=float, default=None)
    p.add_argument("--brightness-clamp", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--transparent-bg", action="store_true",
                    help="Keyed background becomes transparent alpha. Requires .mov output.")
    p.add_argument("--max-frames", type=int, default=None, help="limit frames, for quick tests")
    return p


def main():
    args = build_argparser().parse_args()
    cfg = Config(
        input_path=args.input,
        output_path=args.output,
        cols=args.cols,
        charset=args.charset,
        font_path=args.font_path,
        cell_w=args.cell_w,
        cell_h=args.cell_h,
        key_hex=args.key_hex,
        key_tol_h=args.key_tol_h,
        key_tol_s=args.key_tol_s,
        key_tol_v=args.key_tol_v,
        bg_mode=args.bg_mode,
        bg_char=args.bg_char,
        bg_color_mode=args.bg_color_mode,
        fg_color_mode=args.fg_color_mode,
        bg_color=hex_to_rgb(args.bg_color),
        fg_color=hex_to_rgb(args.fg_color),
        bg_palette=parse_color_list(args.bg_palette),
        fg_palette=parse_color_list(args.fg_palette),
        bpm=args.bpm,
        brightness_clamp=args.brightness_clamp,
        seed=args.seed,
        transparent_bg=args.transparent_bg,
    )
    convert(cfg, max_frames=args.max_frames)


if __name__ == "__main__":
    main()

# --------------------------------------------------------------------------
# To mux your original audio back in afterward:
#   ffmpeg -i output.mp4 -i original_set_audio.wav -c:v copy -map 0:v:0 -map 1:a:0 -shortest final.mp4
# --------------------------------------------------------------------------