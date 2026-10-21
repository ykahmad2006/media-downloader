"""
Video / audio downloader using yt-dlp.

Setup:
    pip install -U yt-dlp

Optional but recommended: FFmpeg, which lets yt-dlp combine the best video
and best audio into one file. Without it, the script falls back to the best
single-file format (often lower resolution).

Only download media you have the right to save.
"""

import os
import shutil

import yt_dlp
from yt_dlp.utils import DownloadError

OUTPUT_FOLDER = "downloads"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def build_options(audio_only):
    """Create the yt-dlp settings for video or audio-only downloads."""
    options = {
        # Short, safe filenames: "Title [id].ext"
        "outtmpl": os.path.join(OUTPUT_FOLDER, "%(title).100B [%(id)s].%(ext)s"),
        "noplaylist": True,       # one video, not a whole playlist
        "nooverwrites": True,     # don't download the same file twice
        "continuedl": True,       # resume partial downloads
        "ignoreerrors": False,    # raise errors instead of hiding them
    }

    if audio_only:
        options["format"] = "bestaudio/best"   # original audio, no re-encoding
    elif HAS_FFMPEG:
        options["format"] = "bestvideo*+bestaudio/best"
        options["merge_output_format"] = "mp4"
    else:
        options["format"] = "best"             # single file, no merging needed

    return options


def download(url, audio_only=False):
    """Download url. Returns the list of files that were really saved."""
    with yt_dlp.YoutubeDL(build_options(audio_only)) as ydl:
        info = ydl.extract_info(url, download=True)

    files = []
    for item in (info or {}).get("requested_downloads") or []:
        path = item.get("filepath")
        if path and os.path.exists(path):
            files.append(path)
    return files


def main():
    if not HAS_FFMPEG:
        print("Note: FFmpeg not found, so video quality may be limited.")

    while True:
        url = input("\nPaste a video URL (or 'q' to quit): ").strip()
        if url.lower() == "q":
            break
        if not url:
            continue

        mode = input("Download [v]ideo or [a]udio only? (v/a): ").strip().lower()
        audio_only = mode == "a"

        print("\nDownloading, please wait...\n")
        try:
            files = download(url, audio_only)
        except DownloadError as error:
            print(f"\nDownload FAILED: {error}")
            print("Tip: run  pip install -U yt-dlp  and try again.")
            continue
        except Exception as error:
            print(f"\nUnexpected error: {error}")
            continue

        if files:
            print("\nSaved:")
            for path in files:
                print("  ", os.path.abspath(path))
        else:
            print("\nNothing was saved.")


if __name__ == "__main__":
    main()