"""
X / Twitter video downloader (uses yt-dlp).

Setup:
    pip install -U yt-dlp

Only download media you have the right to save.
"""

import os
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

# ==============================
# SETTINGS
# ==============================

DOWNLOAD_FOLDER = "X_Downloads"

# If X blocks you (HTTP 403) or the post needs a login, set this to the
# browser you are logged in to X with: "chrome", "firefox", "edge", ...
# Close that browser first. Leave as None to run without cookies.
COOKIES_BROWSER = None

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)


# ==============================
# HELPERS
# ==============================

def is_x_url(url):
    """Check whether the URL belongs to X/Twitter."""
    hostname = urlparse(url).hostname
    if not hostname:
        return False

    hostname = hostname.lower()
    return (
        hostname == "x.com"
        or hostname.endswith(".x.com")
        or hostname == "twitter.com"
        or hostname.endswith(".twitter.com")
    )


def collect_files(info):
    """Return the list of files that were really saved to disk."""
    items = info.get("entries") or [info]  # a post with several videos has entries
    files = []

    for item in items:
        if not item:
            continue
        for download in item.get("requested_downloads") or []:
            path = download.get("filepath")
            if path and os.path.exists(path):
                files.append(path)

    return files


# ==============================
# DOWNLOAD
# ==============================

def download_with_ytdlp(url):
    """Download the post's video(s). Returns a list of saved file paths."""
    options = {
        # Best video + best audio, merged into one file (falls back to "best")
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "format_sort": ["res", "fps", "hdr:12", "codec", "br", "size"],
        "outtmpl": os.path.join(
            DOWNLOAD_FOLDER, "%(uploader)s - %(title).100B [%(id)s].%(ext)s"
        ),
        "nooverwrites": True,
        "continuedl": True,
        # IMPORTANT: False makes failures raise an error instead of being
        # silently ignored, so we never report a fake success.
        "ignoreerrors": False,
        "quiet": False,
    }

    if COOKIES_BROWSER:
        options["cookiesfrombrowser"] = (COOKIES_BROWSER,)

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)

    if not info:
        return []

    return collect_files(info)


# ==============================
# MAIN
# ==============================

def main():
    print("=" * 55)
    print("        X / TWITTER VIDEO DOWNLOADER")
    print("=" * 55)

    url = input("\nPaste the X/Twitter post URL: ").strip()

    if not is_x_url(url):
        print("\nThat does not appear to be an X/Twitter URL.")
        return

    print("\nDownloading the highest available quality...\n")

    try:
        files = download_with_ytdlp(url)
    except DownloadError as error:
        print("\nDownload FAILED.")
        print("Reason:", error)
        print("\nThings to try:")
        print("  1. Update yt-dlp:  pip install -U yt-dlp")
        print("  2. Set COOKIES_BROWSER at the top of this file (e.g. 'chrome')")
        print("  3. Check the post is public and the video still exists")
        return
    except Exception as error:
        print("\nUnexpected error:", error)
        return

    if not files:
        print("\nNothing was saved. The post may not contain a video.")
        print("(This script downloads videos only, not photos.)")
        return

    print("\n" + "=" * 55)
    print("DOWNLOAD FINISHED")
    print("=" * 55)
    print("\nSaved files:")
    for path in files:
        print("  ", os.path.abspath(path))


if __name__ == "__main__":
    main()