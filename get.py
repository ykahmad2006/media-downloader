"""
Command-line downloader for YouTube, X, TikTok, Instagram and more.
Shows the available qualities and sizes, you pick one. Replaces yt.py, downloader.py,
tiktok.py, instagram.py and download_audio.py. Needs fetchly_core.py next to it.
"""

import os
import shutil
import uuid

os.environ.setdefault("MAX_MB", "0")      # the command line has no size limit; the website keeps its own

from enhance import enhance_video, video_height
from fetchly_core import analyze, fetch, overlay

url = input("Paste a link: ").strip()
try:
    found = analyze(url)
except Exception as error:
    raise SystemExit(f"Could not read that link: {error}")

print(f"\n{found['title']}\n")
for i, o in enumerate(found["options"], 1):
    size = f"about {o['size']} MB" if o["size"] else "size unknown"
    print(f"  {i}. {o['label']:<30} {o['detail']:<28} {size}")

total = len(found["options"])
while True:
    answer = input(f"\nPick a number (1-{total}, or q to quit): ").strip().lower()
    if answer == "q":
        raise SystemExit
    if answer.isdigit() and 1 <= int(answer) <= total:
        choice = found["options"][int(answer) - 1]
        break
    print("That is not one of the numbers above.")

folder = os.path.join("downloads", uuid.uuid4().hex[:8])
os.makedirs(folder)
print("\nDownloading, please wait...")
try:
    if choice["id"] == "overlay":                          # tweet over a photo: ask for the photo, the look and the place
        path = input("\nPath to your photo (press Enter to use the tweet's own picture): ").strip().strip('"')
        photo = open(path, "rb").read() if path else None
        theme = input("Look: dark, dim or light [dark]: ").strip().lower() or "dark"
        position = input("Place the card at top, middle or bottom [bottom]: ").strip().lower() or "bottom"
        saved = overlay(url, photo, theme, position, folder)
    else:
        saved = fetch(url, choice["id"], folder)
    print("\nSaved:", os.path.abspath(saved))
except Exception as error:
    raise SystemExit(f"\nDownload FAILED: {error}\nTip: pip install -U yt-dlp gallery-dl  and try again.")

# Small video? Offer a cleaner, bigger copy (FFmpeg only, see enhance.py)
if saved.lower().endswith((".mp4", ".mkv", ".webm", ".mov")) and shutil.which("ffmpeg"):
    height = video_height(saved)
    if 0 < height < 1080:
        if input(f"\nThis video is {height}p. Make an enhanced 1080p copy? (y/n): ").strip().lower() == "y":
            print("Enhancing, this can take a few minutes...")
            try:
                print("Saved:", os.path.abspath(enhance_video(saved, 1080)))
            except Exception as error:
                print("Enhancing failed:", error)