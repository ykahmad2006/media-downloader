"""
Turn a tweet that is only words into a picture made like an ancient parchment scroll:
the author's profile photo as a sepia medallion, the tweet in blackletter with an illuminated first letter,
~username under the face, and the date at the foot.

Used by fetchly_core.py (website) and so by get.py. Needs: pip install pillow fonttools requests
The fonts in the fonts folder are free under the SIL Open Font License (licence files are beside them).
"""

import html
import io
import json
import math
import os
import random
import re
import subprocess
import sys
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse

import requests
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
BODY_FONT = os.path.join(FONTS, "UnifrakturMaguntia-Book.ttf")   # the tweet itself
BOLD_FONT = os.path.join(FONTS, "UnifrakturCook-Bold.ttf")       # ~username and the illuminated letter
ITALIC_FONT = os.path.join(FONTS, "IMFeENit28P.ttf")             # the date line
ROMAN_FONT = os.path.join(FONTS, "IMFeENrm28P.ttf")              # the ~ in front of the username (the blackletter one is tiny)
MAX_CHARS = 1200        # longer tweets are cut with an ellipsis
TOOL_TIMEOUT = 120
W = 1400                # width of the finished picture in pixels

INK = (40, 24, 12)
RED = (104, 28, 20)
GOLD = (190, 148, 56)

LANCZOS = Image.Resampling.LANCZOS
BICUBIC = Image.Resampling.BICUBIC


class CardError(Exception):
    """The tweet cannot be drawn (for example it is written in a script the fonts do not have)."""


# ---------------------------------------------------------------- finding the tweet

STATUS_RE = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:i/web|[^/?#]+)/status/(\d+)", re.I)


def tweet_id(url):
    """The tweet's number if url is a link to one, else None."""
    match = STATUS_RE.match(url or "")
    return match.group(1) if match else None


def _from_gallery(url, tid, extra_args):
    """gallery-dl with its text-tweets option: gives the words, the author and the photo link."""
    cmd = [sys.executable, "-m", "gallery_dl", "-j", "-o", "text-tweets=true", *extra_args, url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gallery-dl took too long")
    except ValueError:
        raise RuntimeError("gallery-dl gave no data. " + result.stderr[-200:])
    for entry in data:   # [2, {...}] is the tweet's own information
        if isinstance(entry, list) and entry and entry[0] == 2 and isinstance(entry[-1], dict) \
                and str(entry[-1].get("tweet_id")) == tid:
            meta = entry[-1]
            author = meta.get("author") or {}
            photos = [e[1] for e in data if isinstance(e, list) and len(e) >= 3 and e[0] == 3 and isinstance(e[1], str)
                      and isinstance(e[-1], dict) and str(e[-1].get("tweet_id")) == tid
                      and str(e[-1].get("extension", "")).lower() in ("jpg", "jpeg", "png", "webp")]
            return {"id": tid, "text": meta.get("content") or "", "handle": author.get("name") or "",
                    "nick": author.get("nick") or "", "avatar": author.get("profile_image") or "",
                    "date": str(meta.get("date") or ""), "media": int(meta.get("count") or 0),
                    "verified": bool(author.get("verified")), "photos": photos, "cover": ""}
    raise RuntimeError("gallery-dl did not return that tweet. " + result.stderr[-200:])


def _syndication_token(tid):
    """The small code X's public embed endpoint expects (same recipe yt-dlp uses)."""
    try:
        from yt_dlp.jsinterp import js_number_to_string
        return js_number_to_string((int(tid) / 1e15) * math.pi, 36).translate(str.maketrans(dict.fromkeys("0.")))
    except Exception:
        return "0"


def _from_syndication(tid):
    """Backup source: the public endpoint X's own embedded tweets use. No login needed."""
    reply = requests.get("https://cdn.syndication.twimg.com/tweet-result",
                         params={"id": tid, "token": _syndication_token(tid)},
                         headers={"User-Agent": "Googlebot"}, timeout=20)
    reply.raise_for_status()
    data = reply.json()
    user = data.get("user") or {}
    if not data.get("text") or not user:
        raise RuntimeError("The embed endpoint had nothing for that tweet.")
    text, shown = data["text"], data.get("display_text_range")
    if isinstance(shown, list) and len(shown) == 2:   # hides the link X adds at the end when media is attached
        text = text[shown[0]:shown[1]]
    media = bool(data.get("mediaDetails") or data.get("photos") or data.get("video"))
    photos = [p["url"] for p in data.get("photos") or [] if p.get("url")]
    photos += [m["media_url_https"] for m in data.get("mediaDetails") or []
               if m.get("type") == "photo" and m.get("media_url_https") and m["media_url_https"] not in photos]
    photos = [u + ":orig" if u.endswith((".jpg", ".jpeg", ".png", ".webp")) else u for u in photos]   # the full size version
    cover = (data.get("video") or {}).get("poster") or next(
        (m.get("media_url_https") for m in data.get("mediaDetails") or [] if m.get("type") in ("video", "animated_gif")), "")
    return {"id": tid, "text": text, "handle": user.get("screen_name") or "", "nick": user.get("name") or "",
            "avatar": (user.get("profile_image_url_https") or "").replace("_normal.", "_400x400."),
            "date": data.get("created_at") or "", "media": int(media),
            "verified": bool(user.get("is_blue_verified") or user.get("verified")), "photos": photos, "cover": cover or ""}


def tweet_info(url, extra_args=()):
    """Everything needed to draw a tweet: text, handle, avatar link, date, and how many media it carries."""
    tid = tweet_id(url)
    if not tid:
        raise RuntimeError("That is not a link to a tweet.")
    try:
        return _from_gallery(url, tid, list(extra_args))
    except Exception as first:
        try:
            return _from_syndication(tid)
        except Exception as second:
            raise RuntimeError(f"Could not read that tweet ({first}; {second})")


# ---------------------------------------------------------------- the words

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


@lru_cache(maxsize=None)
def _cmap(path):
    return set(TTFont(path).getBestCmap().keys())


def clean_text(raw):
    """Tidy the tweet and drop what blackletter cannot draw (emoji, links). Raises CardError if nothing is left."""
    text = html.unescape(raw or "").replace("\r", "").replace("\u00a0", " ")
    text = URL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    have = _cmap(BODY_FONT)
    kept, letters, lost = [], 0, 0
    for ch in text:
        if ch.isalpha():
            letters += 1
        if ch == "\n" or ord(ch) in have:
            kept.append(ch)
        elif ch.isalpha():
            lost += 1
    if letters and lost / letters > 0.4:
        raise CardError("That tweet is mostly in a script the scroll's fonts cannot draw yet.")
    text = re.sub(r" {2,}", " ", "".join(kept))
    text = re.sub(r" ?\n ?", "\n", text).strip()
    if not text:
        raise CardError("There is no text on that tweet that the scroll can write.")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def wrap(text, font, width_of_line):
    """Break text into lines. width_of_line(i) is how wide line number i may be (the first lines sit beside the big letter)."""
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            trial = f"{current} {word}" if current else word
            if font.getlength(trial) <= width_of_line(len(lines)):
                current = trial
                continue
            if current:
                lines.append(current)
            current = word
            while len(current) > 1 and font.getlength(current) > width_of_line(len(lines)):   # a word wider than the line
                cut = len(current)
                while cut > 1 and font.getlength(current[:cut]) > width_of_line(len(lines)):
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines


def pretty_date(raw):
    match = re.match(r"(\d{4})-(\d\d)-(\d\d)", raw or "")
    if not match:
        return ""
    when = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    day = when.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"Penned on the {day}{suffix} day of {when.strftime('%B')}, in the year {when.year}"


# ---------------------------------------------------------------- the materials

def parchment(w, h, rng):
    """Aged paper: blotches, grain, fibres, stains, burnt and ragged edges. Returns an RGBA sheet."""
    base = Image.new("RGB", (w, h), (229, 207, 158))
    small = Image.new("L", (w // 40 + 2, h // 40 + 2))
    small.putdata([rng.randint(60, 200) for _ in range(small.width * small.height)])
    blotch = small.resize((w, h), BICUBIC).filter(ImageFilter.GaussianBlur(25))
    base = Image.composite(Image.new("RGB", (w, h), (150, 105, 50)), base,
                           blotch.point(lambda v: int(max(0, v - 90) * 0.55)))
    grain = ImageOps.colorize(Image.effect_noise((w, h), 26), (150, 110, 60), (255, 240, 200))
    base = Image.blend(base, grain, 0.10)

    marks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mark = ImageDraw.Draw(marks)
    for _ in range(650):                                   # paper fibres
        x, y, a, n = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(0, math.tau), rng.uniform(8, 40)
        mark.line([(x, y), (x + math.cos(a) * n, y + math.sin(a) * n)], fill=(120, 85, 40, rng.randint(25, 60)))
    for _ in range(7):                                     # water stains
        x, y, rx, ry = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(40, 160), rng.uniform(30, 110)
        mark.ellipse([x - rx, y - ry, x + rx, y + ry], fill=(112, 72, 26, rng.randint(22, 48)))
    base = Image.alpha_composite(base.convert("RGBA"), marks.filter(ImageFilter.GaussianBlur(14))).convert("RGB")

    edge, step = 16, 14                                    # ragged outline: a wandering border with the odd tear
    pts, off = [], edge / 2
    def wander(prev):
        return max(0, min(edge, prev + rng.uniform(-3, 3)))
    for x in range(0, w + 1, step):
        off = wander(off)
        pts.append((x, off + (rng.uniform(10, 26) if rng.random() < 0.012 else 0)))
    for y in range(0, h + 1, step):
        off = wander(off)
        pts.append((w - off - (rng.uniform(10, 26) if rng.random() < 0.012 else 0), y))
    for x in range(w, -1, -step):
        off = wander(off)
        pts.append((x, h - off))
    for y in range(h, -1, -step):
        off = wander(off)
        pts.append((off + (rng.uniform(10, 26) if rng.random() < 0.012 else 0), y))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    burn = ImageOps.invert(mask.filter(ImageFilter.GaussianBlur(38))).point(lambda v: min(255, int(v * 1.05)))
    base = Image.composite(Image.new("RGB", (w, h), (86, 52, 20)), base, burn)
    sheet = base.convert("RGBA")
    sheet.putalpha(mask)
    return sheet


def wood(w, h):
    """The dark table the scroll lies on."""
    tone = ImageOps.colorize(Image.linear_gradient("L").resize((w, h)), (22, 14, 8), (48, 33, 21))
    return Image.blend(tone, ImageOps.colorize(Image.effect_noise((w, h), 14), (20, 12, 7), (60, 42, 26)), 0.08).convert("RGBA")


def rod(canvas, x0, x1, yc, h=64):
    """A wooden scroll rod with gold finials, shaded like a cylinder."""
    column = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        light = 0.38 + 0.62 * math.sin(math.pi * min(1, t * 1.15)) ** 0.9 + (0.18 if 0.22 < t < 0.34 else 0)
        column.putpixel((0, y), tuple(min(255, int(c * light)) for c in (148, 98, 50)))
    strip = column.resize((x1 - x0, h))
    ImageDraw.Draw(strip).line([(0, h // 2), (x1 - x0, h // 2)], fill=(96, 62, 30), width=1)
    cut = Image.new("L", strip.size, 0)
    ImageDraw.Draw(cut).rounded_rectangle([0, 0, strip.width - 1, h - 1], radius=16, fill=255)
    canvas.paste(strip, (x0, yc - h // 2), cut)
    draw = ImageDraw.Draw(canvas)
    for x in (x0 - 14, x1 + 14):                           # finials
        draw.ellipse([x - 25, yc - 25, x + 25, yc + 25], fill=(150, 108, 38), outline=(84, 56, 18), width=3)
        draw.ellipse([x - 13, yc - 17, x + 5, yc - 1], fill=(222, 184, 92))


def shade(canvas, x0, x1, y0, y1, top_dark):
    """Soft shadow the rod throws on the paper."""
    gradient = Image.linear_gradient("L").resize((x1 - x0, y1 - y0))
    alpha = (ImageOps.invert(gradient) if top_dark else gradient).point(lambda v: int(v * 0.42))
    canvas.paste(Image.new("RGBA", alpha.size, (30, 16, 6, 255)), (x0, y0), alpha)


def sepia_face(photo, d):
    """The profile photo, cropped square and toned like an old engraving."""
    face = ImageOps.fit(photo.convert("RGB"), (d, d), LANCZOS, centering=(0.5, 0.35))
    face = ImageOps.colorize(ImageOps.autocontrast(face.convert("L"), cutoff=2), (38, 22, 10), (238, 218, 172), mid=(150, 112, 66))
    dark = ImageOps.invert(Image.radial_gradient("L").resize((d, d))).point(lambda v: 255 - v)  # darker towards the rim
    return Image.composite(Image.new("RGB", (d, d), (60, 36, 14)), face, dark.point(lambda v: int(v * 0.45)))


def medallion(canvas, cx, cy, photo, letter, d=224):
    """Profile photo in a round frame: gold ring, ink ring, a ring of dots."""
    pad = 70
    size = d + 2 * pad
    shadow = Image.new("L", (size, size), 0)
    ImageDraw.Draw(shadow).ellipse([pad - 6, pad - 6, pad + d + 6, pad + d + 6], fill=150)
    canvas.paste(Image.new("RGBA", (size, size), (20, 10, 4, 255)), (cx - size // 2 + 5, cy - size // 2 + 9),
                 shadow.filter(ImageFilter.GaussianBlur(10)))
    if photo is not None:
        face = sepia_face(photo, d)
    else:                                                  # no photo: a parchment disc with the first letter
        face = Image.new("RGB", (d, d), (214, 188, 132))
        ImageDraw.Draw(face).text((d // 2, d // 2), letter, font=ImageFont.truetype(BOLD_FONT, int(d * 0.62)),
                                  fill=RED, anchor="mm")
    k = 4                                                  # draw at 4x, shrink: smooth circles
    ring = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    c, r = size * k // 2, d * k // 2
    draw.ellipse([c - r - 24 * k, c - r - 24 * k, c + r + 24 * k, c + r + 24 * k], outline=GOLD + (255,), width=9 * k)
    draw.ellipse([c - r - 31 * k, c - r - 31 * k, c + r + 31 * k, c + r + 31 * k], outline=INK + (255,), width=3 * k)
    draw.ellipse([c - r - 15 * k, c - r - 15 * k, c + r + 15 * k, c + r + 15 * k], outline=INK + (255,), width=2 * k)
    for i in range(36):                                    # beads between the rings
        a = i * math.tau / 36
        px, py = c + math.cos(a) * (r + 19.5 * k), c + math.sin(a) * (r + 19.5 * k)
        draw.ellipse([px - 2.4 * k, py - 2.4 * k, px + 2.4 * k, py + 2.4 * k], fill=INK + (255,))
    ring = ring.resize((size, size), LANCZOS)
    disc = Image.new("L", (d * k, d * k), 0)
    ImageDraw.Draw(disc).ellipse([0, 0, d * k - 1, d * k - 1], fill=255)
    canvas.paste(face, (cx - d // 2, cy - d // 2), disc.resize((d, d), LANCZOS))
    canvas.alpha_composite(ring, (cx - size // 2, cy - size // 2))


def flourish(draw, xc, y, half):
    """A divider: lines running out to a diamond with two small ones either side."""
    def diamond(x, s):
        draw.polygon([(x, y - s), (x + s, y), (x, y + s), (x - s, y)], fill=INK + (255,))
    draw.line([(xc - half, y), (xc - 44, y)], fill=INK + (200,), width=2)
    draw.line([(xc + 44, y), (xc + half, y)], fill=INK + (200,), width=2)
    diamond(xc, 11)
    for dx in (-27, 27):
        diamond(xc + dx, 6)
    for x in (xc - half - 8, xc + half + 8):
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=INK + (255,))


def frame(draw, x0, y0, x1, y1):
    """A double ruled border with a small diamond at each corner."""
    draw.rectangle([x0, y0, x1, y1], outline=INK + (210,), width=3)
    draw.rectangle([x0 + 11, y0 + 11, x1 - 11, y1 - 11], outline=INK + (170,), width=1)
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        draw.polygon([(x, y - 16), (x + 16, y), (x, y + 16), (x - 16, y)], fill=GOLD + (255,), outline=INK + (255,))
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=RED + (255,))


# ---------------------------------------------------------------- putting it together

def fetch_avatar(url):
    """Download the profile photo (only from X's own image servers, at most 8 MB). None if it cannot be had."""
    host = urlparse(url or "").hostname or ""
    if not (url or "").startswith("https://") or not (host == "twimg.com" or host.endswith(".twimg.com")):
        return None
    try:
        reply = requests.get(url, timeout=15, stream=True, headers={"User-Agent": "Mozilla/5.0"})
        reply.raise_for_status()
        data = reply.raw.read(8 * 1024 * 1024 + 1, decode_content=True)
        return data if len(data) <= 8 * 1024 * 1024 else None
    except Exception:
        return None


def render_card(info, out_path, avatar_bytes=None):
    """Draw the scroll for a tweet (the dict from tweet_info) and save it as a PNG. Returns out_path."""
    text = clean_text(info.get("text"))
    handle = (info.get("handle") or "unknown").lstrip("@")
    rng = random.Random(int(info.get("id") or 0) or 7)

    photo = None
    raw = avatar_bytes if avatar_bytes is not None else fetch_avatar(info.get("avatar"))
    if raw:
        try:
            photo = Image.open(io.BytesIO(raw))
            photo.load()
        except Exception:
            photo = None

    # type size follows the length of the tweet: short ones are written large
    size = 66 if len(text) <= 90 else 56 if len(text) <= 180 else 48 if len(text) <= 320 else 42
    body, line_h = ImageFont.truetype(BODY_FONT, size), int(size * 1.4)
    left, right = 140, W - 140                              # content edges inside the border
    col_w = 330                                             # the face column
    text_x, text_w = left + col_w + 55, right - (left + col_w + 55)

    cap = text[0] if len(text) >= 30 and text[0].isalpha() else None   # the illuminated first letter
    box = line_h * 2 - 10
    indent = box + 22 if cap else 0
    lines = wrap(text[1:] if cap else text, body, lambda i: text_w - (indent if cap and i < 2 else 0))
    if cap and lines and not lines[0]:
        lines = lines[1:] or [""]
    text_h = max(len(lines), 2 if cap else 1) * line_h

    name_size = 46
    def name_fonts(n):
        return ImageFont.truetype(BOLD_FONT, n), ImageFont.truetype(ROMAN_FONT, int(n * 1.7))
    name_font, tilde_font = name_fonts(name_size)
    while name_size > 22 and tilde_font.getlength("~") + 4 + name_font.getlength(handle) > col_w:
        name_size -= 2
        name_font, tilde_font = name_fonts(name_size)
    face_h = 290 + 22 + int(name_size * 1.3)
    content_h = max(text_h, face_h)

    date_line = pretty_date(info.get("date"))
    date_font = ImageFont.truetype(ITALIC_FONT, 34)
    while date_line and date_font.getlength(date_line) > right - left and date_font.size > 18:
        date_font = ImageFont.truetype(ITALIC_FONT, date_font.size - 2)

    top = 175                                               # first content line, under the top rod
    flourish_y = top + content_h + 55
    date_y = flourish_y + 38
    foot = date_y + (46 if date_line else 0)
    sheet_h = foot + 105
    H = sheet_h + 106
    sheet_w, sheet_x, sheet_y = W - 120, 60, 53

    canvas = wood(W, H)
    shadow_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow_mask).rectangle([sheet_x + 10, sheet_y + 14, sheet_x + sheet_w + 10, sheet_y + sheet_h + 14], fill=190)
    canvas.paste(Image.new("RGBA", (W, H), (8, 4, 2, 255)), (0, 0), shadow_mask.filter(ImageFilter.GaussianBlur(16)))
    paper = parchment(sheet_w, sheet_h, rng)
    canvas.alpha_composite(paper, (sheet_x, sheet_y))
    shade(canvas, sheet_x, sheet_x + sheet_w, sheet_y + 20, sheet_y + 74, True)
    shade(canvas, sheet_x, sheet_x + sheet_w, sheet_y + sheet_h - 74, sheet_y + sheet_h - 20, False)
    rod(canvas, 30, W - 30, 53)
    rod(canvas, 30, W - 30, sheet_y + sheet_h)

    marks = Image.new("RGBA", (W, H), (0, 0, 0, 0))         # border and ornaments
    mdraw = ImageDraw.Draw(marks)
    frame(mdraw, sheet_x + 46, sheet_y + 62, sheet_x + sheet_w - 46, sheet_y + sheet_h - 62)
    flourish(mdraw, W // 2, flourish_y, 330)
    canvas.alpha_composite(marks)

    face_cx = left + col_w // 2
    medallion(canvas, face_cx, top + 145, photo, handle[:1].upper() or "?")

    text_y = top + max(0, (content_h - text_h) // 2)
    if cap:                                                 # gold panel behind the big first letter
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(panel)
        pdraw.rectangle([text_x, text_y + 4, text_x + box, text_y + 4 + box], fill=(206, 164, 66, 255), outline=INK + (255,), width=3)
        pdraw.rectangle([text_x + 8, text_y + 12, text_x + box - 8, text_y + 4 + box - 8], outline=(120, 80, 20, 255), width=2)
        canvas.alpha_composite(panel)

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))           # all writing goes on one layer, then blurs slightly like ink
    draw = ImageDraw.Draw(ink)
    if cap:
        draw.text((text_x + box // 2, text_y + 4 + box // 2 + 4), cap, font=ImageFont.truetype(BOLD_FONT, int(box * 0.88)),
                  fill=RED + (255,), anchor="mm")
    for i, line in enumerate(lines):
        draw.text((text_x + (indent if cap and i < 2 else 0), text_y + i * line_h), line, font=body, fill=INK + (255,))
    tilde_w = tilde_font.getlength("~") + 4                  # ~ then the username, together centred under the face
    name_x = face_cx - (tilde_w + name_font.getlength(handle)) / 2
    baseline = top + 290 + 22 + name_font.getmetrics()[0]
    draw.text((name_x, baseline), "~", font=tilde_font, fill=RED + (255,), anchor="ls")
    draw.text((name_x + tilde_w, baseline), handle, font=name_font, fill=RED + (255,), anchor="ls")
    if date_line:
        draw.text((W // 2, date_y), date_line, font=date_font, fill=(70, 44, 20, 255), anchor="ma")
    soft = ink.filter(ImageFilter.GaussianBlur(1.3))
    soft.putalpha(soft.getchannel("A").point(lambda v: int(v * 0.55)))
    canvas.alpha_composite(soft)
    canvas.alpha_composite(ink)

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path