"""
Fetchly engine: analyse a link, list every download option, fetch the one chosen.
Used by app.py (website) and get.py / instagram.py (command line).

Setup: pip install -U flask yt-dlp gallery-dl
FFmpeg is recommended: it enables HD merging, MP3 and GIF options.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import traceback

import yt_dlp
from yt_dlp.utils import DownloadError

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_GALLERY = importlib.util.find_spec("gallery_dl") is not None
COOKIES_BROWSER = None  # "chrome", "firefox" or "edge": the browser you're logged in with (Instagram stories, private posts)
MAX_MB = 1000

# name: (width px, frames per second, seconds, label)
GIF_PRESETS = {
    "gif_s": (320, 10, 6, "Small GIF"),
    "gif_m": (480, 12, 8, "Medium GIF"),
    "gif_l": (640, 15, 10, "Large GIF"),
}


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".opus", ".wav")


def opt(oid, kind, label, detail, size):
    return {"id": oid, "kind": kind, "label": label, "detail": detail, "size": size}


def mb(n):
    return round(n / 1048576, 1) if n else None


def ydl_opts(extra=None):
    o = {"quiet": True, "noplaylist": True, "ignoreerrors": False}
    if COOKIES_BROWSER:
        o["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    return {**o, **(extra or {})}


def build_options(info):
    """Turn yt-dlp's format list into simple choices with estimated sizes."""
    fmts = info.get("formats") or []
    dur = info.get("duration") or 0
    has = lambda f, key: f.get(key) not in (None, "none")

    def size(f):
        s = f.get("filesize") or f.get("filesize_approx")
        if not s and f.get("tbr") and dur:
            s = f["tbr"] * 125 * dur
        return s or 0

    audio = [f for f in fmts if has(f, "acodec") and not has(f, "vcodec")]
    best_audio = max(audio, key=lambda f: f.get("abr") or 0, default=None)
    audio_size = size(best_audio) if best_audio else 0

    heights = {}
    for f in fmts:
        if f.get("height") and has(f, "vcodec"):
            total = size(f) + (0 if has(f, "acodec") else audio_size)
            heights[f["height"]] = max(heights.get(f["height"], 0), total)

    options = [opt(f"v{h}", "video", f"{h}p video", "MP4, with sound", mb(heights[h]))
               for h in sorted(heights, reverse=True)]
    if best_audio:
        options.append(opt("a_orig", "audio", "Original audio", "Best quality, M4A or Opus", mb(audio_size)))
    if HAS_FFMPEG:
        if dur:
            options.append(opt("a_mp3", "audio", "MP3 audio", "Plays on any phone", mb(dur * 40000)))
        if heights:
            for oid, (w, fps, secs, name) in GIF_PRESETS.items():
                length = min(dur or secs, secs)
                est = w * (w * 9 // 16) * fps * length * 0.3
                options.append(opt(oid, "gif", name, f"{w}px wide, first {int(length)}s", mb(est)))
    return options


def analyze(url):
    """Look at a link without downloading. Returns title, thumbnail and options."""
    info, options = {}, []
    try:
        with yt_dlp.YoutubeDL(ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        options = build_options(info)
    except DownloadError:
        if not HAS_GALLERY:
            raise
    # Photos, slideshows, carousels and stories come from gallery-dl
    if HAS_GALLERY and ("instagram.com" in url or not any(o["kind"] == "video" for o in options)):
        slides = not any(o["kind"] == "video" for o in options)
        if not HAS_FFMPEG:
            label, detail = "All photos and videos in this post", "Saved as a ZIP"
        elif slides and "instagram.com" not in url:
            label, detail = "Slideshow video (MP4)", "Your photos set to the original song"
        else:
            label, detail = "Everything in this post", "Photo slideshows come as an MP4, mixed posts as a ZIP"
        options.append(opt("gallery", "zip", label, detail, None))
    if not options:
        raise DownloadError("No downloadable media found.")
    return {"title": info.get("title") or "Your link", "thumb": info.get("thumbnail"),
            "duration": int(info.get("duration") or 0), "by": info.get("uploader") or "", "options": options}


def first_file(folder):
    files = [os.path.join(folder, n) for n in os.listdir(folder) if not n.endswith((".part", ".ytdl"))]
    if not files:
        raise DownloadError("Nothing was saved. The file may be bigger than the limit.")
    return max(files, key=os.path.getsize)


def slideshow(url, images, song, job_dir):
    """Photos plus the post's song become one MP4 (1080x1920 for TikTok, 1080x1350 otherwise)."""
    tmp = job_dir + "_tmp"
    os.makedirs(tmp, exist_ok=True)
    try:
        duration = 0
        if not song:
            try:  # slideshow posts carry their song as the audio track
                song_opts = ydl_opts({"format": "bestaudio/best", "outtmpl": os.path.join(tmp, "song.%(ext)s")})
                with yt_dlp.YoutubeDL(song_opts) as ydl:
                    duration = (ydl.extract_info(url, download=True) or {}).get("duration") or 0
                song = first_file(tmp)
            except Exception:
                song = None
        per = min(5, max(2, duration / len(images))) if duration else 3  # seconds per photo
        lines = []
        for path in images:
            lines.append("file '%s'\nduration %.2f" % (path.replace("\\", "/").replace("'", "'\\''"), per))
        lines.append("file '%s'" % images[-1].replace("\\", "/").replace("'", "'\\''"))
        list_path = os.path.join(tmp, "list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        w, h = (1080, 1920) if "tiktok.com" in url else (1080, 1350)
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
        out = os.path.join(job_dir, "slideshow.mp4")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path]
        if song:
            cmd += ["-i", song]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        if song:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-movflags", "+faststart", out]
        subprocess.run(cmd, check=True, capture_output=True)

        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gallery(url, job_dir):
    """gallery-dl: every photo/video of a post. All photos become an MP4 slideshow, otherwise a ZIP."""
    cmd = [sys.executable, "-m", "gallery_dl", "-d", job_dir]
    if COOKIES_BROWSER:
        cmd += ["--cookies-from-browser", COOKIES_BROWSER]
    result = subprocess.run(cmd + [url], capture_output=True, text=True)
    files = [os.path.join(p, n) for p, _, names in os.walk(job_dir) for n in names]
    if not files:
        raise DownloadError("gallery-dl saved nothing. " + result.stderr[-200:])
    if len(files) == 1:
        return files[0]
    images = sorted(f for f in files if f.lower().endswith(IMAGE_EXT))
    songs = [f for f in files if f.lower().endswith(AUDIO_EXT)]
    if HAS_FFMPEG and images and len(images) + len(songs) == len(files):
        try:
            return slideshow(url, images, songs[0] if songs else None, job_dir)
        except Exception:
            traceback.print_exc()  # fall back to the ZIP below
    return shutil.make_archive(job_dir + "_zip", "zip", job_dir)


def make_gif(url, oid, job_dir, opts):
    width, fps, secs, _ = GIF_PRESETS[oid]
    opts.update({"format": "bestvideo*[height<=720]/best[height<=720]/best",
                 "outtmpl": os.path.join(job_dir, "source.%(ext)s")})
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    source = first_file(job_dir)
    out = os.path.join(job_dir, "clip.gif")
    vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
    subprocess.run(["ffmpeg", "-y", "-t", str(secs), "-i", source, "-vf", vf, out],
                   check=True, capture_output=True)
    os.remove(source)
    return out


def fetch(url, oid, job_dir):
    """Download one chosen option into job_dir and return the file path."""
    if oid == "gallery":
        return gallery(url, job_dir)
    opts = ydl_opts({
        "outtmpl": os.path.join(job_dir, "%(title).100B [%(id)s].%(ext)s"),
        "max_filesize": MAX_MB * 1048576,
        "format_sort": ["res", "vcodec:h264", "acodec:aac"],  # top resolution, then the most compatible codec
    })
    if oid.startswith("v"):
        h = int(oid[1:])
        opts["format"] = (f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]" if HAS_FFMPEG
                          else f"best[height<={h}]")
        opts["merge_output_format"] = "mp4"
    elif oid == "a_orig":
        opts["format"] = "bestaudio/best"
    elif oid == "a_mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
    elif oid in GIF_PRESETS:
        return make_gif(url, oid, job_dir, opts)
    else:
        raise DownloadError("Unknown option.")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return first_file(job_dir)