#!/usr/bin/env python3
import contextlib
import os
import queue
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from Converter.ascii_vj import Config, ConversionCancelled, convert, hex_to_rgb, parse_color_list
from Remux.audio_remux_simple import RemuxCancelled, find_ffmpeg, remux_audio


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


class QueueWriter:
	def __init__(self, output_queue):
		self.output_queue = output_queue

	def write(self, text):
		if text:
			self.output_queue.put(text)

	def flush(self):
		pass


class ToolTip:
	def __init__(self, widget, text):
		self.widget = widget
		self.text = text
		self.tip_window = None
		self.after_id = None
		self.widget.bind("<Enter>", self._schedule)
		self.widget.bind("<Leave>", self._hide)
		self.widget.bind("<ButtonPress>", self._hide)

	def _schedule(self, _event=None):
		self._cancel()
		self.after_id = self.widget.after(500, self._show)

	def _cancel(self):
		if self.after_id is not None:
			self.widget.after_cancel(self.after_id)
			self.after_id = None

	def _show(self):
		if self.tip_window or not self.text:
			return
		x = self.widget.winfo_rootx() + 18
		y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
		self.tip_window = tw = tk.Toplevel(self.widget)
		tw.wm_overrideredirect(True)
		tw.wm_geometry(f"+{x}+{y}")
		label = tk.Label(
			tw,
			text=self.text,
			justify=tk.LEFT,
			background="#fff8cc",
			relief=tk.SOLID,
			borderwidth=1,
			padx=6,
			pady=4,
			wraplength=320,
		)
		label.pack()

	def _hide(self, _event=None):
		self._cancel()
		if self.tip_window is not None:
			self.tip_window.destroy()
			self.tip_window = None


class AsciiVJApp:
	def __init__(self, root):
		self.root = root
		self.root.title("Twig's ASCII VJ Converter")
		self.root.geometry("980x760")

		self.log_queue = queue.Queue()
		self.worker_thread = None
		self.cancel_event = threading.Event()

		self._build_variables()
		self._build_ui()
		self._apply_defaults()
		self._pump_logs()

	def _build_variables(self):
		self.input_path = tk.StringVar()
		self.output_path = tk.StringVar()
		self.remux_enabled = tk.BooleanVar(value=False)
		self.remux_output_path = tk.StringVar()
		self.tooltips = []

		self.cols = tk.IntVar(value=120)
		self.charset = tk.StringVar(value=" .:-=+*#%@")
		self.font_path = tk.StringVar()
		self.cell_w = tk.IntVar(value=10)
		self.cell_h = tk.IntVar(value=18)

		self.key_hex = tk.StringVar(value="00ff00")
		self.key_tol_h = tk.IntVar(value=25)
		self.key_tol_s = tk.IntVar(value=100)
		self.key_tol_v = tk.IntVar(value=70)

		self.bg_mode = tk.StringVar(value="whitespace")
		self.bg_char = tk.StringVar(value=".")
		self.bg_color_mode = tk.StringVar(value="static")
		self.fg_color_mode = tk.StringVar(value="source")
		self.bg_color = tk.StringVar(value="0f0f14")
		self.fg_color = tk.StringVar(value="ffffff")
		self.bg_palette = tk.StringVar(value="ff0050,00ffff,ffff00")
		self.fg_palette = tk.StringVar(value="ff0050,00ffff,ffff00")

		self.bpm = tk.StringVar()
		self.auto_beats = tk.BooleanVar(value=False)
		self.brightness_clamp = tk.DoubleVar(value=1.0)
		self.seed = tk.StringVar()
		self.max_frames = tk.StringVar()

	def _add_tooltip(self, text, *widgets):
		for widget in widgets:
			if widget is not None:
				self.tooltips.append(ToolTip(widget, text))

	def _build_ui(self):
		main = ttk.Frame(self.root, padding=12)
		main.pack(fill=tk.BOTH, expand=True)

		title = ttk.Label(main, text="Twig's ASCII VJ Converter", font=("Segoe UI", 18, "bold"))
		title.pack(anchor=tk.W)
		self._add_tooltip("Desktop UI for converting green-screen video into ASCII art and optionally remuxing audio.", title)

		notebook = ttk.Notebook(main)
		notebook.pack(fill=tk.BOTH, expand=True, pady=(10, 10))

		job_tab = ttk.Frame(notebook, padding=10)
		style_tab = ttk.Frame(notebook, padding=10)
		advanced_tab = ttk.Frame(notebook, padding=10)
		notebook.add(job_tab, text="Job")
		notebook.add(style_tab, text="Style")
		notebook.add(advanced_tab, text="Advanced")

		self._build_job_tab(job_tab)
		self._build_style_tab(style_tab)
		self._build_advanced_tab(advanced_tab)

		log_frame = ttk.LabelFrame(main, text="Log", padding=8)
		log_frame.pack(fill=tk.BOTH, expand=True)
		self.log_text = tk.Text(log_frame, height=14, wrap="word", state="disabled")
		log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
		self.log_text.configure(yscrollcommand=log_scroll.set)
		self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
		self._add_tooltip("Shows conversion progress, warnings, and remux output while jobs are running.", log_frame, self.log_text, log_scroll)

		actions = ttk.Frame(main)
		actions.pack(fill=tk.X, pady=(10, 0))
		self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel_job, state="disabled")
		self.cancel_button.pack(side=tk.RIGHT, padx=(0, 8))
		self._add_tooltip("Request cancellation of the active conversion or remux job.", self.cancel_button)
		self.run_button = ttk.Button(actions, text="Convert", command=self.start_job)
		self.run_button.pack(side=tk.RIGHT)
		self._add_tooltip("Start the ASCII conversion job using the current settings. If remux is enabled, audio will be added after conversion.", self.run_button)

	def _build_job_tab(self, parent):
		io_frame = ttk.LabelFrame(parent, text="Files", padding=10)
		io_frame.pack(fill=tk.X)
		io_frame.columnconfigure(1, weight=1)

		input_label = ttk.Label(io_frame, text="Input video")
		input_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
		input_entry = ttk.Entry(io_frame, textvariable=self.input_path)
		input_entry.grid(row=0, column=1, sticky="ew", pady=4)
		input_browse = ttk.Button(io_frame, text="Browse...", command=self.browse_input)
		input_browse.grid(row=0, column=2, padx=(8, 0), pady=4)
		self._add_tooltip("Choose the source video to convert into ASCII art.", input_label, input_entry, input_browse)

		output_label = ttk.Label(io_frame, text="ASCII output")
		output_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
		output_entry = ttk.Entry(io_frame, textvariable=self.output_path)
		output_entry.grid(row=1, column=1, sticky="ew", pady=4)
		output_browse = ttk.Button(io_frame, text="Browse...", command=self.browse_output)
		output_browse.grid(row=1, column=2, padx=(8, 0), pady=4)
		self._add_tooltip("Choose where the converted ASCII video will be saved.", output_label, output_entry, output_browse)

		remux_toggle = ttk.Checkbutton(
			io_frame,
			text="Remux original audio after conversion",
			variable=self.remux_enabled,
			command=self._sync_remux_state,
		)
		remux_toggle.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 4))
		self._add_tooltip("After conversion, copy the original audio track onto the generated video and save a second file.", remux_toggle)

		remux_label = ttk.Label(io_frame, text="Remuxed output")
		remux_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
		self.remux_entry = ttk.Entry(io_frame, textvariable=self.remux_output_path)
		self.remux_entry.grid(row=3, column=1, sticky="ew", pady=4)
		self.remux_browse = ttk.Button(io_frame, text="Browse...", command=self.browse_remux_output)
		self.remux_browse.grid(row=3, column=2, padx=(8, 0), pady=4)
		self._add_tooltip("Choose where the remuxed video with audio should be written.", remux_label, self.remux_entry, self.remux_browse)

		quick_frame = ttk.LabelFrame(parent, text="Quick settings", padding=10)
		quick_frame.pack(fill=tk.X, pady=(10, 0))
		for idx in range(4):
			quick_frame.columnconfigure(idx, weight=1)

		cols_label = ttk.Label(quick_frame, text="Columns")
		cols_label.grid(row=0, column=0, sticky="w", pady=4)
		cols_entry = ttk.Entry(quick_frame, textvariable=self.cols, width=10)
		cols_entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Number of ASCII columns. Higher values give more detail but take longer to process.", cols_label, cols_entry)

		fg_mode_label = ttk.Label(quick_frame, text="Foreground color mode")
		fg_mode_label.grid(row=0, column=2, sticky="w", pady=4)
		fg_mode_combo = ttk.Combobox(
			quick_frame,
			textvariable=self.fg_color_mode,
			values=["source", "static", "beat-cycle", "beat-random-per-char"],
			state="readonly",
		)
		fg_mode_combo.grid(row=0, column=3, sticky="ew", pady=4)
		self._add_tooltip("Choose how foreground character colors are generated. 'source' matches the original video colors.", fg_mode_label, fg_mode_combo)

		bg_mode_label = ttk.Label(quick_frame, text="Background mode")
		bg_mode_label.grid(row=1, column=0, sticky="w", pady=4)
		bg_mode_combo = ttk.Combobox(
			quick_frame,
			textvariable=self.bg_mode,
			values=["whitespace", "char", "random", "solid"],
			state="readonly",
		)
		bg_mode_combo.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Choose how keyed background cells are filled: blank, a fixed character, random characters, or a solid color.", bg_mode_label, bg_mode_combo)

		bg_char_label = ttk.Label(quick_frame, text="Background char")
		bg_char_label.grid(row=1, column=2, sticky="w", pady=4)
		bg_char_entry = ttk.Entry(quick_frame, textvariable=self.bg_char, width=10)
		bg_char_entry.grid(row=1, column=3, sticky="ew", pady=4)
		self._add_tooltip("Character used when background mode is set to 'char'.", bg_char_label, bg_char_entry)


	def _build_style_tab(self, parent):
		color_frame = ttk.LabelFrame(parent, text="Colors", padding=10)
		color_frame.pack(fill=tk.X)
		for idx in range(4):
			color_frame.columnconfigure(idx, weight=1)

		fg_color_label = ttk.Label(color_frame, text="Foreground color")
		fg_color_label.grid(row=0, column=0, sticky="w", pady=4)
		fg_color_entry = ttk.Entry(color_frame, textvariable=self.fg_color)
		fg_color_entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Static foreground color in hex, used when foreground mode is 'static'.", fg_color_label, fg_color_entry)

		bg_color_label = ttk.Label(color_frame, text="Background color")
		bg_color_label.grid(row=0, column=2, sticky="w", pady=4)
		bg_color_entry = ttk.Entry(color_frame, textvariable=self.bg_color)
		bg_color_entry.grid(row=0, column=3, sticky="ew", pady=4)
		self._add_tooltip("Background color in hex. Also used for solid background mode or static background color mode.", bg_color_label, bg_color_entry)

		fg_palette_label = ttk.Label(color_frame, text="Foreground palette")
		fg_palette_label.grid(row=1, column=0, sticky="w", pady=4)
		fg_palette_entry = ttk.Entry(color_frame, textvariable=self.fg_palette)
		fg_palette_entry.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Comma-separated hex colors used by beat-based foreground color modes.", fg_palette_label, fg_palette_entry)

		bg_palette_label = ttk.Label(color_frame, text="Background palette")
		bg_palette_label.grid(row=1, column=2, sticky="w", pady=4)
		bg_palette_entry = ttk.Entry(color_frame, textvariable=self.bg_palette)
		bg_palette_entry.grid(row=1, column=3, sticky="ew", pady=4)
		self._add_tooltip("Comma-separated hex colors used by beat-based background color modes.", bg_palette_label, bg_palette_entry)

		bg_color_mode_label = ttk.Label(color_frame, text="Background color mode")
		bg_color_mode_label.grid(row=2, column=0, sticky="w", pady=4)
		bg_color_mode_combo = ttk.Combobox(
			color_frame,
			textvariable=self.bg_color_mode,
			values=["static", "beat-cycle", "beat-random-per-char"],
			state="readonly",
		)
		bg_color_mode_combo.grid(row=2, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Choose how background colors change over time for keyed cells.", bg_color_mode_label, bg_color_mode_combo)

		bpm_label = ttk.Label(color_frame, text="BPM")
		bpm_label.grid(row=2, column=2, sticky="w", pady=4)
		self.bpm_entry = ttk.Entry(color_frame, textvariable=self.bpm)
		self.bpm_entry.grid(row=2, column=3, sticky="ew", pady=4)
		self._add_tooltip("Beats per minute for beat-synced color changes. Leave empty for no beat sync. Disabled when auto-detect beats is on.", bpm_label, self.bpm_entry)

		auto_beats_toggle = ttk.Checkbutton(
			color_frame,
			text="Auto-detect beats from audio",
			variable=self.auto_beats,
			command=self._sync_beat_mode_state,
		)
		auto_beats_toggle.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
		self._add_tooltip("Detect beat timestamps directly from the input video's audio track instead of assuming a fixed BPM. Requires librosa (pip install librosa soundfile).", auto_beats_toggle)

		glyph_frame = ttk.LabelFrame(parent, text="Glyphs", padding=10)
		glyph_frame.pack(fill=tk.X, pady=(10, 0))
		for idx in range(4):
			glyph_frame.columnconfigure(idx, weight=1)

		charset_label = ttk.Label(glyph_frame, text="Charset")
		charset_label.grid(row=0, column=0, sticky="w", pady=4)
		charset_entry = ttk.Entry(glyph_frame, textvariable=self.charset)
		charset_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
		self._add_tooltip("Characters used from darkest to brightest when mapping video luminance into ASCII.", charset_label, charset_entry)

		cell_w_label = ttk.Label(glyph_frame, text="Cell width")
		cell_w_label.grid(row=1, column=0, sticky="w", pady=4)
		cell_w_entry = ttk.Entry(glyph_frame, textvariable=self.cell_w)
		cell_w_entry.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Pixel width of each rendered character cell in the output video.", cell_w_label, cell_w_entry)

		cell_h_label = ttk.Label(glyph_frame, text="Cell height")
		cell_h_label.grid(row=1, column=2, sticky="w", pady=4)
		cell_h_entry = ttk.Entry(glyph_frame, textvariable=self.cell_h)
		cell_h_entry.grid(row=1, column=3, sticky="ew", pady=4)
		self._add_tooltip("Pixel height of each rendered character cell in the output video.", cell_h_label, cell_h_entry)

		font_label = ttk.Label(glyph_frame, text="Font path (optional)")
		font_label.grid(row=2, column=0, sticky="w", pady=4)
		font_entry = ttk.Entry(glyph_frame, textvariable=self.font_path)
		font_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
		font_browse = ttk.Button(glyph_frame, text="Browse...", command=self.browse_font)
		font_browse.grid(row=2, column=3, pady=4, padx=(8, 0))
		self._add_tooltip("Optional path to a monospace font file. Leave empty to use the auto-detected default font.", font_label, font_entry, font_browse)

	def _build_advanced_tab(self, parent):
		key_frame = ttk.LabelFrame(parent, text="Keying", padding=10)
		key_frame.pack(fill=tk.X)
		for idx in range(6):
			key_frame.columnconfigure(idx, weight=1)

		key_color_label = ttk.Label(key_frame, text="Key color")
		key_color_label.grid(row=0, column=0, sticky="w", pady=4)
		key_color_entry = ttk.Entry(key_frame, textvariable=self.key_hex)
		key_color_entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Hex color to treat as the background key color, usually green screen.", key_color_label, key_color_entry)

		hue_tol_label = ttk.Label(key_frame, text="Hue tol")
		hue_tol_label.grid(row=0, column=2, sticky="w", pady=4)
		hue_tol_entry = ttk.Entry(key_frame, textvariable=self.key_tol_h)
		hue_tol_entry.grid(row=0, column=3, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Hue tolerance around the key color. Increase this if background pixels are not being removed cleanly.", hue_tol_label, hue_tol_entry)

		sat_tol_label = ttk.Label(key_frame, text="Sat tol")
		sat_tol_label.grid(row=0, column=4, sticky="w", pady=4)
		sat_tol_entry = ttk.Entry(key_frame, textvariable=self.key_tol_s)
		sat_tol_entry.grid(row=0, column=5, sticky="ew", pady=4)
		self._add_tooltip("Minimum saturation required for a pixel to count as keyed background.", sat_tol_label, sat_tol_entry)

		val_tol_label = ttk.Label(key_frame, text="Val tol")
		val_tol_label.grid(row=1, column=0, sticky="w", pady=4)
		val_tol_entry = ttk.Entry(key_frame, textvariable=self.key_tol_v)
		val_tol_entry.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Minimum brightness required for a pixel to count as keyed background.", val_tol_label, val_tol_entry)

		run_frame = ttk.LabelFrame(parent, text="Run options", padding=10)
		run_frame.pack(fill=tk.X, pady=(10, 0))
		for idx in range(4):
			run_frame.columnconfigure(idx, weight=1)

		brightness_label = ttk.Label(run_frame, text="Brightness clamp")
		brightness_label.grid(row=0, column=0, sticky="w", pady=4)
		brightness_entry = ttk.Entry(run_frame, textvariable=self.brightness_clamp)
		brightness_entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Scales randomly generated colors down for safety. 1.0 keeps full brightness, lower values dim the output.", brightness_label, brightness_entry)

		seed_label = ttk.Label(run_frame, text="Seed (optional)")
		seed_label.grid(row=0, column=2, sticky="w", pady=4)
		seed_entry = ttk.Entry(run_frame, textvariable=self.seed)
		seed_entry.grid(row=0, column=3, sticky="ew", pady=4)
		self._add_tooltip("Optional random seed for reproducible random colors and background patterns.", seed_label, seed_entry)

		max_frames_label = ttk.Label(run_frame, text="Max frames (optional)")
		max_frames_label.grid(row=1, column=0, sticky="w", pady=4)
		max_frames_entry = ttk.Entry(run_frame, textvariable=self.max_frames)
		max_frames_entry.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 12))
		self._add_tooltip("Optional frame limit for quick tests. Leave empty to process the whole video.", max_frames_label, max_frames_entry)

	def _apply_defaults(self):
		default_in = BASE_DIR / "in" / "Golden-GreenScreen.mp4"
		default_out = BASE_DIR / "out" / "output.mp4"
		default_remux = BASE_DIR / "Remuxed" / "output_with_audio.mp4"
		if default_in.exists():
			self.input_path.set(str(default_in))
		self.output_path.set(str(default_out))
		self.remux_output_path.set(str(default_remux))
		self._sync_remux_state()
		self._sync_beat_mode_state()

	def _sync_remux_state(self):
		state = "normal" if self.remux_enabled.get() else "disabled"
		self.remux_entry.configure(state=state)
		self.remux_browse.configure(state=state)

	def _sync_beat_mode_state(self):
		self.bpm_entry.configure(state="disabled" if self.auto_beats.get() else "normal")

	def browse_input(self):
		path = filedialog.askopenfilename(
			title="Select input video",
			initialdir=str(BASE_DIR),
			filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv"), ("All files", "*.*")],
		)
		if path:
			self.input_path.set(path)
			input_path = Path(path)
			out_dir = BASE_DIR / "out"
			remux_dir = BASE_DIR / "Remuxed"
			self.output_path.set(str(out_dir / f"{input_path.stem}_ascii.mp4"))
			self.remux_output_path.set(str(remux_dir / f"{input_path.stem}_ascii_with_audio.mp4"))

	def browse_output(self):
		path = filedialog.asksaveasfilename(
			title="Select output video",
			initialdir=str(BASE_DIR / "out"),
			defaultextension=".mp4",
			filetypes=[("MP4 files", "*.mp4"), ("All files", "*.*")],
		)
		if path:
			self.output_path.set(path)

	def browse_remux_output(self):
		path = filedialog.asksaveasfilename(
			title="Select remuxed output video",
			initialdir=str(BASE_DIR / "Remuxed"),
			defaultextension=".mp4",
			filetypes=[("MP4 files", "*.mp4"), ("All files", "*.*")],
		)
		if path:
			self.remux_output_path.set(path)

	def browse_font(self):
		path = filedialog.askopenfilename(
			title="Select monospace font",
			initialdir=os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts",
			filetypes=[("Fonts", "*.ttf *.ttc *.otf"), ("All files", "*.*")],
		)
		if path:
			self.font_path.set(path)

	def append_log(self, text):
		self.log_text.configure(state="normal")
		self.log_text.insert(tk.END, text)
		self.log_text.see(tk.END)
		self.log_text.configure(state="disabled")

	def _pump_logs(self):
		while True:
			try:
				text = self.log_queue.get_nowait()
			except queue.Empty:
				break
			self.append_log(text)
		self.root.after(100, self._pump_logs)

	def validate(self):
		input_path = Path(self.input_path.get().strip())
		output_path = Path(self.output_path.get().strip())
		remux_output = Path(self.remux_output_path.get().strip()) if self.remux_output_path.get().strip() else None

		if not self.input_path.get().strip():
			raise ValueError("Input video is required.")
		if not input_path.exists():
			raise ValueError(f"Input video not found: {input_path}")
		if not self.output_path.get().strip():
			raise ValueError("ASCII output path is required.")
		if output_path.suffix.lower() != ".mp4":
			raise ValueError("ASCII output should be an .mp4 file.")
		if self.remux_enabled.get() and not self.remux_output_path.get().strip():
			raise ValueError("Remuxed output path is required when remuxing is enabled.")
		if self.remux_enabled.get() and remux_output is not None and remux_output.suffix.lower() != ".mp4":
			raise ValueError("Remuxed output should be an .mp4 file.")
		if self.bg_mode.get() == "char" and not self.bg_char.get():
			raise ValueError("Background char mode requires a background character.")

		self._build_config()

	def start_job(self):
		if self.worker_thread and self.worker_thread.is_alive():
			messagebox.showinfo("ASCII VJ Converter", "A conversion is already running.")
			return

		try:
			self.validate()
		except Exception as exc:
			messagebox.showerror("Invalid settings", str(exc))
			return

		self.cancel_event.clear()
		self.run_button.configure(state="disabled")
		self.cancel_button.configure(state="normal")
		self.append_log("\n=== Starting job ===\n")
		self.worker_thread = threading.Thread(target=self._run_job, daemon=True)
		self.worker_thread.start()

	def cancel_job(self):
		if not self.worker_thread or not self.worker_thread.is_alive():
			return
		self.cancel_event.set()
		self.cancel_button.configure(state="disabled")
		self.append_log("\nCancellation requested...\n")

	def _run_job(self):
		writer = QueueWriter(self.log_queue)
		success = False
		cancelled = False
		try:
			with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
				self._run_conversion()
				if self.remux_enabled.get() and not self.cancel_event.is_set():
					self._run_remux()
			success = True
		except (ConversionCancelled, RemuxCancelled) as exc:
			cancelled = True
			self.log_queue.put(f"\n{exc}\n")
			self._cleanup_cancelled_outputs()
		except Exception as exc:
			self.log_queue.put(f"\nERROR: {exc}\n")
		finally:
			self.root.after(0, self._reset_action_buttons)
			if success:
				self.root.after(0, lambda: messagebox.showinfo("ASCII VJ Converter", "Job completed successfully."))
			elif cancelled:
				self.root.after(0, lambda: messagebox.showinfo("ASCII VJ Converter", "Job cancelled."))
			else:
				self.root.after(0, lambda: messagebox.showerror("ASCII VJ Converter", "Job failed. See log for details."))

	def _reset_action_buttons(self):
		self.run_button.configure(state="normal")
		self.cancel_button.configure(state="disabled")

	def _cleanup_cancelled_outputs(self):
		for value in (self.output_path.get().strip(), self.remux_output_path.get().strip()):
			if not value:
				continue
			path = Path(value)
			if path.exists():
				with contextlib.suppress(OSError):
					path.unlink()

	def _build_config(self):
		input_path = str(Path(self.input_path.get().strip()))
		output_path = str(Path(self.output_path.get().strip()))
		bpm = float(self.bpm.get()) if self.bpm.get().strip() else None
		seed = int(self.seed.get()) if self.seed.get().strip() else None
		max_frames = int(self.max_frames.get()) if self.max_frames.get().strip() else None
		font_path = self.font_path.get().strip() or None

		cfg = Config(
			input_path=input_path,
			output_path=output_path,
			cols=int(self.cols.get()),
			charset=self.charset.get(),
			font_path=font_path,
			cell_w=int(self.cell_w.get()),
			cell_h=int(self.cell_h.get()),
			key_hex=self.key_hex.get().strip(),
			key_tol_h=int(self.key_tol_h.get()),
			key_tol_s=int(self.key_tol_s.get()),
			key_tol_v=int(self.key_tol_v.get()),
			bg_mode=self.bg_mode.get(),
			bg_char=self.bg_char.get() or ".",
			bg_color_mode=self.bg_color_mode.get(),
			fg_color_mode=self.fg_color_mode.get(),
			bg_color=hex_to_rgb(self.bg_color.get().strip()),
			fg_color=hex_to_rgb(self.fg_color.get().strip()),
			bg_palette=parse_color_list(self.bg_palette.get().strip()),
			fg_palette=parse_color_list(self.fg_palette.get().strip()),
			bpm=bpm,
			auto_beats=self.auto_beats.get(),
			brightness_clamp=float(self.brightness_clamp.get()),
			seed=seed,
			transparent_bg=False,
		)
		return cfg, max_frames

	def _run_conversion(self):
		output_path = str(Path(self.output_path.get().strip()))
		output_dir = Path(output_path).parent
		output_dir.mkdir(parents=True, exist_ok=True)
		output_file = Path(output_path)
		if output_file.exists():
			output_file.unlink()

		cfg, max_frames = self._build_config()
		convert(cfg, max_frames=max_frames, cancel_event=self.cancel_event)

	def _run_remux(self):
		input_path = str(Path(self.input_path.get().strip()))
		ascii_path = str(Path(self.output_path.get().strip()))
		remux_path = Path(self.remux_output_path.get().strip())
		remux_path.parent.mkdir(parents=True, exist_ok=True)
		if remux_path.exists():
			remux_path.unlink()

		ffmpeg_exe = find_ffmpeg()
		if not ffmpeg_exe:
			raise RuntimeError("FFmpeg not found. Install imageio-ffmpeg or FFmpeg before remuxing.")

		if not remux_audio(input_path, ascii_path, str(remux_path), ffmpeg_exe, cancel_event=self.cancel_event):
			raise RuntimeError("Audio remux failed.")


def main():
	root = tk.Tk()
	app = AsciiVJApp(root)
	root.mainloop()


if __name__ == "__main__":
	main()
