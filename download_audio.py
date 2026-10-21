"""
Download audio from a URL in the best available quality using yt-dlp.

Only use this for audio you have the right to download: your own uploads,
Creative Commons or public-domain music, the Internet Archive, the YouTube
Audio Library, Free Music Archive, podcasts that allow downloads, etc.

Setup:
    pip install yt-dlp

Run:
    python download_audio.py
"""

import shutil

from yt_dlp import YoutubeDL

OUTPUT_DIR = "downloads"


def download(url, convert_to_mp3=False):
    """Download the best audio stream from url into the downloads folder."""
    options = {
        # Pick the highest-quality audio stream the site offers.
        "format": "bestaudio/best",
        "outtmpl": f"{OUTPUT_DIR}/%(title)s.%(ext)s",
        "noplaylist": True,  # a single video/track, not the whole playlist
    }

    if convert_to_mp3:
        # Optional: re-encode to mp3 (needs FFmpeg). Quality "0" = best mp3.
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ]

    with YoutubeDL(options) as ydl:
        ydl.download([url])


def main():
    has_ffmpeg = shutil.which("ffmpeg") is not None

    while True:
        url = input("\nPaste a URL (or 'q' to quit): ").strip()
        if url.lower() == "q":
            break
        if not url:
            continue

        convert = False
        if has_ffmpeg:
            answer = input("Convert to mp3? (y/n, n keeps original quality): ")
            convert = answer.strip().lower() == "y"

        try:
            download(url, convert_to_mp3=convert)
            print(f"Done! Check the '{OUTPUT_DIR}' folder.")
        except Exception as error:
            print(f"Download failed: {error}")


if __name__ == "__main__":
    main()