import os
import re
import yt_dlp
from urllib.parse import urlparse


# ==============================
# SETTINGS
# ==============================

DOWNLOAD_FOLDER = "TikTok_Downloads"

VIDEO_FOLDER = os.path.join(DOWNLOAD_FOLDER, "Videos")

for folder in [VIDEO_FOLDER]:
    os.makedirs(folder, exist_ok=True)


# ==============================
# HELPERS
# ==============================

def clean_filename(name):
    """Remove characters Windows does not allow in filenames."""

    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = name.strip()

    if not name:
        name = "TikTok_video"

    return name[:180]


def is_tiktok_url(url):
    """Check whether the URL belongs to TikTok."""

    hostname = urlparse(url).hostname

    if not hostname:
        return False

    hostname = hostname.lower()

    return (
        hostname == "tiktok.com"
        or hostname.endswith(".tiktok.com")
    )


# ==============================
# DOWNLOAD TIKTOK
# ==============================

def download_tiktok(url):

    print("\nChecking TikTok...")
    print("Finding the highest available quality...\n")

    options = {
    "format": "bestvideo*+bestaudio/best",
    "merge_output_format": "mp4",

    "outtmpl": os.path.join(
        VIDEO_FOLDER,
        "%(uploader)s - %(title)s [%(id)s].%(ext)s"
    ),

    "postprocessors": [
        {
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }
    ],

    "postprocessor_args": [
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ],

    "nooverwrites": True,
    "continuedl": True,
    "ignoreerrors": True,
    "quiet": False,

    "format_sort": [
        "res",
        "fps",
        "hdr:12",
        "codec",
        "br",
        "size",
    ],
}
    try:

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            if not info:

                print("\nNo downloadable video was found.")

                return False

            print("\nDownload completed!")

            return True

    except Exception as error:

        print("\nDownload failed.")

        print("Reason:", error)

        return False


# ==============================
# MAIN
# ==============================

def main():

    print("=" * 55)
    print("           TIKTOK DOWNLOADER")
    print("=" * 55)

    print("\nSupports:")
    print("  [1] TikTok videos")
    print("  [2] Highest available quality")
    print("  [3] Video + audio")
    print()

    url = input(
        "Paste the TikTok URL: "
    ).strip()

    if not is_tiktok_url(url):

        print(
            "\nThat does not appear to be a TikTok URL."
        )

        return

    print(
        "\nDownloading the highest available quality..."
    )

    success = download_tiktok(url)

    if success:

        print("\n" + "=" * 55)
        print("DOWNLOAD FINISHED")
        print("=" * 55)

        print("\nFiles are located in:")

        print(
            os.path.abspath(DOWNLOAD_FOLDER)
        )

    else:

        print(
            "\nThe TikTok video could not be downloaded."
        )

        print(
            "\nTry updating yt-dlp:"
        )

        print(
            "python -m pip install -U yt-dlp"
        )


if __name__ == "__main__":
    main()