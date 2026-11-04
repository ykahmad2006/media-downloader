

import os
import shutil
import subprocess
import sys

TARGETS = (720, 1080)


def video_height(path):
    """Height in pixels of the first video stream, or 0 if it cannot be read."""
    if not shutil.which("ffprobe"):
        return 0
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return int(result.stdout.strip().split(",")[0])
    except (ValueError, IndexError):
        return 0


def enhanced_name(path):
    base, _ = os.path.splitext(path)
    out, n = f"{base}_enhanced.mp4", 2
    while os.path.exists(out):
        out, n = f"{base}_enhanced_{n}.mp4", n + 1
    return out


def enhance_video(path, target=1080):
    """Write an enhanced copy next to the original and return its path (None if nothing to do)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed.")
    if target not in TARGETS:
        raise ValueError(f"Target must be one of {TARGETS}.")
    height = video_height(path)
    if not height:
        raise RuntimeError("Could not read that video.")
    if height >= target:
        return None
    out = enhanced_name(path)
    # 1 light denoise  2 smooth enlargement (lanczos)  3 gentle sharpening
    vf = f"hqdn3d=1.5:1.5:6:6,scale=-2:{target}:flags=lanczos,unsharp=5:5:0.6:5:5:0.0,format=yuv420p"
    cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-stats", "-i", path,
           "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "17",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else input("Path to the video file: ").strip().strip('"')
    if not os.path.isfile(path):
        raise SystemExit("That file does not exist.")
    answer = sys.argv[2] if len(sys.argv) > 2 else input("Target height, 720 or 1080 [1080]: ").strip() or "1080"
    if not answer.isdigit() or int(answer) not in TARGETS:
        raise SystemExit("Please type 720 or 1080.")
    try:
        out = enhance_video(path, int(answer))
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Enhancing failed: {error}")
    print("Already that tall or taller, nothing to do." if out is None else f"Saved: {os.path.abspath(out)}")


if __name__ == "__main__":
    main()