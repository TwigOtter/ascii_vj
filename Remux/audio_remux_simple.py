#!/usr/bin/env python3
"""
audio_remux_simple.py - Extract audio from source video and mux with ASCII video output

This script uses FFmpeg binary from imageio_ffmpeg to remux audio.
"""
import os
import sys
import subprocess
import time
from pathlib import Path

class RemuxCancelled(Exception):
	pass


def find_ffmpeg():
	"""Find FFmpeg binary installed by imageio_ffmpeg."""
	try:
		import imageio_ffmpeg
		ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
		return ffmpeg_path
	except Exception:
		# Fallback to system FFmpeg
		for cmd in ["ffmpeg", "ffmpeg.exe"]:
			try:
				result = subprocess.run([cmd, "-version"], capture_output=True)
				if result.returncode == 0:
					return cmd
			except:
				pass
	return None


def remux_audio(input_video_path, ascii_video_path, output_path, ffmpeg_exe, cancel_event=None):
	"""
	Remux audio from input video into ASCII video using FFmpeg.

	Args:
		input_video_path: Original video file (audio source)
		ascii_video_path: ASCII art video file (video source)
		output_path: Output video file with audio
		ffmpeg_exe: Path to FFmpeg executable

	Returns:
		True if successful, False otherwise
	"""

	# Convert to Path objects
	input_path = Path(input_video_path)
	ascii_path = Path(ascii_video_path)
	output = Path(output_path)

	# Verify input files exist
	if not input_path.exists():
		print(f"Error: Input video not found: {input_path}")
		return False

	if not ascii_path.exists():
		print(f"Error: ASCII video not found: {ascii_path}")
		return False

	# Create output directory if needed
	output.parent.mkdir(parents=True, exist_ok=True)

	# Check if output exists
	if output.exists():
		response = input(f"Output file exists: {output}\nOverwrite? (y/n): ").strip().lower()
		if response != 'y':
			print("Cancelled.")
			return False
		output.unlink()  # Delete existing file

	print("Audio Remuxer")
	print("=============")
	print(f"Video Input:  {input_path}")
	print(f"ASCII Video:  {ascii_path}")
	print(f"FFmpeg:       {ffmpeg_exe}")
	print(f"Output:       {output}")
	print()
	print("Remuxing audio with video...")
	print()

	try:
		# FFmpeg command to copy video stream and encode audio stream
		# -i first_input: ASCII video (video source)
		# -i second_input: Original video (audio source)
		# -c:v copy: Copy video without re-encoding (fast)
		# -c:a aac: Encode audio as AAC
		# -b:a 192k: Audio bitrate
		# -map 0:v:0: Use video from first input
		# -map 1:a:0: Use audio from second input
		# -shortest: Stop when shortest input ends
		# -y: Overwrite output file

		cmd = [
			ffmpeg_exe,
			"-i", str(ascii_path),        # ASCII video (video source)
			"-i", str(input_path),         # Original video (audio source)
			"-c:v", "copy",                # Copy video codec (no re-encode)
			"-c:a", "aac",                 # Use AAC for audio
			"-b:a", "192k",                # Audio bitrate
			"-map", "0:v:0",               # Map video from first input
			"-map", "1:a:0",               # Map audio from second input
			"-shortest",                   # Stop at shortest input
			"-y",                          # Overwrite output
			str(output)
		]

		process = subprocess.Popen(cmd)
		while True:
			return_code = process.poll()
			if return_code is not None:
				if return_code != 0:
					raise subprocess.CalledProcessError(return_code, cmd)
				break
			if cancel_event is not None and cancel_event.is_set():
				process.terminate()
				try:
					process.wait(timeout=5)
				except subprocess.TimeoutExpired:
					process.kill()
					process.wait()
				if output.exists():
					output.unlink()
				raise RemuxCancelled("Audio remux cancelled by user.")
			time.sleep(0.2)

		# Show file info
		if output.exists():
			output_size_mb = output.stat().st_size / (1024 * 1024)
			print()
			print("✓ Remuxing completed successfully!")
			print(f"Output file: {output}")
			print(f"File size: {output_size_mb:.2f} MB")
			return True
		else:
			print("Error: Output file was not created")
			return False

	except subprocess.CalledProcessError as e:
		print(f"✗ FFmpeg error (exit code {e.returncode})")
		return False
	except Exception as e:
		print(f"✗ Error: {e}")
		return False


def main():
	# Get script directory
	script_dir = Path(__file__).parent.resolve()
	os.chdir(script_dir)

	# Parse arguments
	input_video = "in/Golden-GreenScreen.mp4"
	ascii_video = "out/output.mp4"
	output_file = "out/output_with_audio.mp4"

	if len(sys.argv) > 1:
		input_video = sys.argv[1]
	if len(sys.argv) > 2:
		ascii_video = sys.argv[2]
	if len(sys.argv) > 3:
		output_file = sys.argv[3]

	# Convert relative paths to absolute
	input_path = Path(input_video)
	if not input_path.is_absolute():
		input_path = script_dir / input_path

	ascii_path = Path(ascii_video)
	if not ascii_path.is_absolute():
		ascii_path = script_dir / ascii_path

	output_path = Path(output_file)
	if not output_path.is_absolute():
		output_path = script_dir / output_path

	# Find FFmpeg
	ffmpeg_exe = find_ffmpeg()
	if not ffmpeg_exe:
		print("Error: FFmpeg not found")
		print("Please install it: pip install imageio-ffmpeg")
		sys.exit(1)

	# Run remuxing
	try:
		success = remux_audio(str(input_path), str(ascii_path), str(output_path), ffmpeg_exe)
	except RemuxCancelled as exc:
		print(exc)
		sys.exit(1)
	sys.exit(0 if success else 1)


if __name__ == "__main__":
	main()
