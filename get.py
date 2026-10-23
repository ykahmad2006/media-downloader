"""
Command-line downloader for YouTube, X, TikTok, Instagram and more.
Shows the available qualities and sizes, you pick one. Replaces yt.py, downloader.py,
tiktok.py, instagram.py and download_audio.py. Needs fetchly_core.py next to it.
"""

import os
import uuid

from fetchly_core import analyze, fetch

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
    print("\nSaved:", os.path.abspath(fetch(url, choice["id"], folder)))
except Exception as error:
    print("\nDownload FAILED:", error)
    print("Tip: pip install -U yt-dlp gallery-dl  and try again.")