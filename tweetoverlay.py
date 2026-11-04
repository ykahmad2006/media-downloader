"""
Tweet over a photo: draws a tweet the way X draws it (round profile photo, bold name with the verified badge,
grey @handle, the words with blue @mentions and #hashtags) as a card across a photo.
The photo keeps its own shape. Themes: dark, dim, light. Position: top, middle, bottom.

The card is built from a REAL tweet fetched by its link (see tweetcard.tweet_info), never from typed-in words.
Emoji are drawn with Twemoji (the set X uses), CC-BY 4.0, fetched once and cached.
Needs: pip install pillow fonttools requests
"""

import io
import math
import os
import re
import tempfile
import time
from functools import lru_cache
from urllib.parse import urlparse

import requests
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, ImageOps

import tweetcard

CardError = tweetcard.CardError
FONT = os.path.join(tweetcard.FONTS, "Inter-Variable.ttf")      # Inter, SIL Open Font License
TWEMOJI = "https://raw.githubusercontent.com/jdecked/twemoji/main/assets/72x72"
MAX_UPLOAD = 15 * 1024 * 1024
MAX_SIDE, MIN_WIDTH = 4096, 720            # big photos are shrunk to this, tiny ones are enlarged so the card stays readable
Image.MAX_IMAGE_PIXELS = 80_000_000        # refuse "decompression bomb" files
BLUE = (29, 155, 240)

THEMES = {
    "dark":  {"bg": (0, 0, 0),       "border": (255, 255, 255), "name": (231, 233, 234), "text": (231, 233, 234), "handle": (113, 118, 123)},
    "dim":   {"bg": (21, 32, 43),    "border": (136, 153, 166), "name": (247, 249, 249), "text": (247, 249, 249), "handle": (139, 152, 165)},
    "light": {"bg": (255, 255, 255), "border": (207, 217, 222), "name": (15, 20, 25),    "text": (15, 20, 25),    "handle": (83, 100, 113)},
}
POSITIONS = ("top", "middle", "bottom")


# ---------------------------------------------------------------- fonts and emoji

@lru_cache(maxsize=64)
def font(size, weight=400):
    f = ImageFont.truetype(FONT, max(6, int(size)))
    f.set_variation_by_axes([20, weight])          # [optical size, weight]
    return f


@lru_cache(maxsize=None)
def _cmap():
    return set(TTFont(FONT).getBestCmap().keys())


# emoji that show as pictures even without the VS16 marker
_DEFAULT_BMP = set("⌚⌛⏩⏪⏫⏬⏰⏳◽◾☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕")
_EMOJI_RE = re.compile("|".join([
    r"[\U0001F1E6-\U0001F1FF]{2}",
    r"[#*0-9]\uFE0F?\u20E3",
    r"(?:[\U0001F000-\U0001FAFF\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u21AA\u231A-\u23FF\u24C2\u25AA-\u25FE\u2600-\u27BF"
    r"\u2934\u2935\u2B05-\u2B55\u3030\u303D\u3297\u3299]\uFE0F?[\U0001F3FB-\U0001F3FF]?)"
    r"(?:\u200D(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\u2640\u2642\u2695\u2696\u2708\u2764]\uFE0F?[\U0001F3FB-\U0001F3FF]?))*",
]))
_emoji_down_until = 0.0


def _is_emoji(cluster):
    first = cluster[0]
    return (ord(first) >= 0x1F000 or "\uFE0F" in cluster or "\u200D" in cluster or "\u20E3" in cluster
            or first in _DEFAULT_BMP)


@lru_cache(maxsize=512)
def emoji_image(cluster):
    """The Twemoji picture for one emoji, from the local cache or the web. None if unavailable."""
    global _emoji_down_until
    codes = [f"{ord(c):x}" for c in cluster]
    if "200d" not in codes:
        codes = [c for c in codes if c != "fe0f"]
    name = "-".join(codes)
    folder = os.path.join(tempfile.gettempdir(), "fetchly_emoji")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".png")
    if not os.path.exists(path):
        if time.time() < _emoji_down_until:
            return None
        try:
            reply = requests.get(f"{TWEMOJI}/{name}.png", timeout=6)
            if reply.status_code == 404:
                return None
            reply.raise_for_status()
            with open(path, "wb") as f:
                f.write(reply.content)
        except Exception:
            _emoji_down_until = time.time() + 60           # network trouble: skip emoji for a minute instead of waiting on each
            return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


# ---------------------------------------------------------------- the words

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


def clean(raw):
    """Tidy the tweet: unescape, drop links, drop letters the font lacks. Raises CardError if nothing readable is left."""
    import html
    text = html.unescape(raw or "").replace("\r", "").replace("\u00a0", " ")
    text = _URL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    have = _cmap()
    out, letters, lost, pos = [], 0, 0, 0
    for m in _EMOJI_RE.finditer(text):
        for ch in text[pos:m.start()]:
            letters += ch.isalpha()
            if ch == "\n" or ord(ch) in have:
                out.append(ch)
            elif ch.isalpha():
                lost += 1
        piece = m.group(0)
        if _is_emoji(piece):
            out.append(piece)
        else:                                             # a plain symbol the regex also matched (for example ©)
            out.extend(ch for ch in piece if ord(ch) in have)
        pos = m.end()
    for ch in text[pos:]:
        letters += ch.isalpha()
        if ch == "\n" or ord(ch) in have:
            out.append(ch)
        elif ch.isalpha():
            lost += 1
    if letters and lost / letters > 0.4:
        raise CardError("That tweet is mostly in a script this card's font cannot draw yet.")
    text = re.sub(r" {2,}", " ", "".join(out))
    text = re.sub(r" ?\n ?", "\n", text).strip()
    if not text:
        raise CardError("That tweet has no text the card can write.")
    return text[:1500]


def _segments(word, normal, link):
    """Split one word into pieces: ("t", text, colour) or ("e", emoji, None). @mentions and #tags are blue."""
    parts, entity = [], re.match(r"([@#$]\w+)(.*)", word, re.S)
    chunks = [(entity.group(1), link), (entity.group(2), normal)] if entity else [(word, normal)]
    for chunk, colour in chunks:
        pos = 0
        for m in _EMOJI_RE.finditer(chunk):
            if _is_emoji(m.group(0)):
                if m.start() > pos:
                    parts.append(("t", chunk[pos:m.start()], colour))
                parts.append(("e", m.group(0), None))
                pos = m.end()
        if pos < len(chunk):
            parts.append(("t", chunk[pos:], colour))
    return parts


def _seg_width(seg, fnt, size):
    return fnt.getlength(seg[1]) if seg[0] == "t" else size * 1.12


def _word_width(word, fnt, size):
    return sum(_seg_width(s, fnt, size) for s in word)


def layout(text, fnt, size, max_w, normal, link):
    """Break the tweet into lines. A line is a list of words; a word is a list of pieces."""
    space = fnt.getlength(" ")
    lines = []
    for paragraph in text.split("\n"):
        current, used = [], 0.0
        for raw in paragraph.split(" "):
            if not raw:
                continue
            word = _segments(raw, normal, link)
            width = _word_width(word, fnt, size)
            if width > max_w and all(s[0] == "t" for s in word):      # one huge word: cut it into pieces that fit
                joined, colour = "".join(s[1] for s in word), word[0][2]
                pieces, buf = [], ""
                for ch in joined:
                    if fnt.getlength(buf + ch) > max_w and buf:
                        pieces.append(buf)
                        buf = ""
                    buf += ch
                pieces.append(buf)
                for i, piece in enumerate(pieces):
                    if current:
                        lines.append(current)
                    current, used = [[("t", piece, colour)]], fnt.getlength(piece)
                continue
            need = width if not current else space + width
            if current and used + need > max_w:
                lines.append(current)
                current, used, need = [], 0.0, width
            current.append(word)
            used += need
        lines.append(current)
    return lines


def _line_width(line, fnt, size):
    space = fnt.getlength(" ")
    return sum(_word_width(w, fnt, size) for w in line) + space * max(0, len(line) - 1)


def shorten(lines, max_lines, fnt, size, max_w, normal):
    """Keep at most max_lines, ending with an ellipsis."""
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = [list(word) for word in lines[-1]]
    dots = ("t", "…", normal)                                  # joined to the last word, with no space before it
    while last and _line_width(last[:-1] + [last[-1] + [dots]], fnt, size) > max_w:
        last = last[:-1]
    lines[-1] = last[:-1] + [last[-1] + [dots]] if last else [[dots]]
    return lines


# ---------------------------------------------------------------- drawing pieces

def draw_pieces(canvas, draw, x, y, line, fnt, size, line_h):
    """Draw one line of words starting at x, with y the top of the line."""
    space = fnt.getlength(" ")
    asc, desc = fnt.getmetrics()
    base = y + (line_h - (asc + desc)) / 2 + asc
    for i, word in enumerate(line):
        if i:
            x += space
        for kind, value, colour in word:
            if kind == "t":
                draw.text((x, base), value, font=fnt, fill=colour + (255,), anchor="ls")
            else:
                pic = emoji_image(value)
                side = int(round(size * 1.12))
                if pic is not None:
                    pic = pic.resize((side, side), Image.Resampling.LANCZOS)
                    canvas.alpha_composite(pic, (int(round(x)), int(round(y + (line_h - side) / 2 + size * 0.04))))
            x += _seg_width((kind, value, colour), fnt, size)
    return x


def verified_badge(size):
    """The blue wavy seal with a white tick, as a size x size picture."""
    k = 8
    big = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c, r = size * k / 2, size * k / 2 * 0.97
    pts = [(c + math.cos(a) * r * (0.9 + 0.1 * math.cos(8 * a)), c + math.sin(a) * r * (0.9 + 0.1 * math.cos(8 * a)))
           for a in (i * math.tau / 360 for i in range(360))]
    d.polygon(pts, fill=BLUE + (255,))
    tick = [(c - r * 0.42, c + r * 0.02), (c - r * 0.12, c + r * 0.34), (c + r * 0.44, c - r * 0.28)]
    d.line(tick, fill=(255, 255, 255, 255), width=int(size * k * 0.11), joint="curve")
    for p in (tick[0], tick[-1]):
        w = size * k * 0.055
        d.ellipse([p[0] - w, p[1] - w, p[0] + w, p[1] + w], fill=(255, 255, 255, 255))
    return big.resize((size, size), Image.Resampling.LANCZOS)


def round_picture(photo, diameter, fallback_letter, theme):
    """The profile photo cut into a circle (or a grey disc with the first letter when there is no photo)."""
    k = 4
    if photo is not None:
        face = ImageOps.fit(photo.convert("RGB"), (diameter * k, diameter * k), Image.Resampling.LANCZOS)
    else:
        face = Image.new("RGB", (diameter * k, diameter * k), theme["handle"])
        ImageDraw.Draw(face).text((diameter * k / 2, diameter * k / 2), fallback_letter, font=font(diameter * k * 0.5, 700),
                                  fill=theme["bg"], anchor="mm")
    mask = Image.new("L", (diameter * k, diameter * k), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter * k - 1, diameter * k - 1], fill=255)
    out = face.convert("RGBA")
    out.putalpha(mask)
    return out.resize((diameter, diameter), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------- photos

def open_photo(data):
    """Check and prepare the photo: right way up, RGB, not huge, not tiny. Raises CardError with a reason to show."""
    if len(data) > MAX_UPLOAD:
        raise CardError("That photo is bigger than 15 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        kind = img.format
        if kind not in ("JPEG", "MPO", "PNG", "WEBP", "GIF"):
            raise CardError("Please use a JPG, PNG or WEBP photo.")
        img.load()
    except CardError:
        raise
    except Exception:
        raise CardError("That file is not a photo we can open.")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        img = flat
    else:
        img = img.convert("RGB")
    longest = max(img.size)
    if longest > MAX_SIDE:
        factor = MAX_SIDE / longest
        img = img.resize((max(1, int(img.width * factor)), max(1, int(img.height * factor))), Image.Resampling.LANCZOS)
    elif img.width < MIN_WIDTH:
        factor = MIN_WIDTH / img.width
        img = img.resize((MIN_WIDTH, int(img.height * factor)), Image.Resampling.LANCZOS)
    return img


def fetch_own_photo(info):
    """The tweet's own first photo, or the cover picture of its video, as bytes (only from X's image servers, at most 20 MB)."""
    urls = list(info.get("photos") or []) or ([info["cover"]] if info.get("cover") else [])
    if not urls:
        raise CardError("That tweet has no picture we can use. Please upload a photo.")
    url = urls[0]
    host = urlparse(url).hostname or ""
    if not url.startswith("https://") or not (host == "twimg.com" or host.endswith(".twimg.com")):
        raise CardError("We could not get that tweet's photo.")
    try:
        reply = requests.get(url, timeout=20, stream=True, headers={"User-Agent": "Mozilla/5.0"})
        reply.raise_for_status()
        data = reply.raw.read(20 * 1024 * 1024 + 1, decode_content=True)
    except Exception:
        raise CardError("We could not get that tweet's photo.")
    if len(data) > 20 * 1024 * 1024:
        raise CardError("That tweet's photo is too large.")
    return data


# ---------------------------------------------------------------- the card

def render_overlay(info, photo_bytes, out_path, theme="dark", position="bottom", avatar_bytes=None):
    """Draw the tweet across the photo and save a high quality JPEG. Returns out_path."""
    if theme not in THEMES:
        raise CardError("Pick Dark, Dim or Light.")
    if position not in POSITIONS:
        raise CardError("Pick Top, Middle or Bottom.")
    colours = THEMES[theme]
    text = clean(info.get("text"))
    photo = open_photo(photo_bytes)
    W, H = photo.size
    base = min(W, H * 0.81)                                  # a 4:5 portrait is sized by its width, wider photos by their height
    s = base / 1088.0                                        # every size below is designed at 1088 px wide
    ss = 2 if base <= 2400 else 1                            # draw at double size and shrink: smooth edges

    avatar = None
    raw = avatar_bytes if avatar_bytes is not None else tweetcard.fetch_avatar(info.get("avatar"))
    if raw:
        try:
            avatar = Image.open(io.BytesIO(raw))
            avatar.load()
        except Exception:
            avatar = None
    handle = (info.get("handle") or "").lstrip("@")
    shown_name = (info.get("nick") or handle or "Unknown").strip()
    verified = bool(info.get("verified"))

    pad_x = round(24 * s)
    card_w = min(W - 2 * round(52 * s), round(984 * s))     # a tweet-sized card, centred
    margin_x = (W - card_w) // 2
    text_w = (card_w - 2 * pad_x) * ss
    avatar_d, gap = round(115 * s), round(22 * s)

    # choose the biggest text size whose card fits in 60% of the photo's height
    chosen = None
    for factor in (1.0, 0.9, 0.8, 0.7, 0.62, 0.55):
        size = round(40 * s * factor) * ss
        fnt = font(size, 400)
        lines = layout(text, fnt, size, text_w, colours["text"], BLUE)
        line_h = round(size * 1.32)
        head_h = avatar_d * ss
        card_h = (round(30 * s) + round(40 * s) + round(34 * s)) * ss + head_h + len(lines) * line_h
        if card_h <= H * 0.6 * ss or factor == 0.55:
            chosen = (size, fnt, lines, line_h, head_h)
            break
    size, fnt, lines, line_h, head_h = chosen
    max_lines = max(1, int((H * 0.6 * ss - (round(30 * s) + round(40 * s) + round(34 * s)) * ss - head_h) // line_h))
    lines = shorten(lines, max_lines, fnt, size, text_w, colours["text"])
    card_h = (round(30 * s) + round(40 * s) + round(34 * s)) * ss + head_h + len(lines) * line_h

    layer = Image.new("RGBA", (card_w * ss, card_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, card_w * ss - 1, card_h - 1], radius=round(30 * s) * ss, fill=colours["bg"] + (255,),
                        outline=colours["border"] + (255,), width=max(1, round(2 * s)) * ss)

    top, left = round(30 * s) * ss, pad_x * ss
    layer.alpha_composite(round_picture(avatar, avatar_d * ss, (handle[:1] or shown_name[:1] or "?").upper(), colours), (left, top))

    # name (bold) with the badge, and the handle under it, centred beside the picture
    name_x = left + (avatar_d + gap) * ss
    room = card_w * ss - pad_x * ss - name_x - (round(42 * s) * ss if verified else 0)
    name_size = round(40 * s) * ss
    name_font = font(name_size, 700)
    name_word = _segments(shown_name, colours["name"], colours["name"])
    while _word_width(name_word, name_font, name_size) > room and name_size > round(26 * s) * ss:
        name_size -= ss
        name_font = font(name_size, 700)
    if _word_width(name_word, name_font, name_size) > room:     # still too long: trim and end with an ellipsis
        trimmed = shown_name
        while len(trimmed) > 1 and _word_width(_segments(trimmed + "…", colours["name"], colours["name"]), name_font, name_size) > room:
            trimmed = trimmed[:-1]
        name_word = _segments(trimmed + "…", colours["name"], colours["name"])
    handle_size = round(36 * s) * ss
    handle_font = font(handle_size, 400)
    name_line, handle_line = round(name_size * 1.3), round(handle_size * 1.3)
    block_top = top + (head_h - (name_line + handle_line)) // 2
    end_x = draw_pieces(layer, d, name_x, block_top, [name_word], name_font, name_size, name_line)
    if verified:
        badge = round(34 * s) * ss
        layer.alpha_composite(verified_badge(badge), (int(end_x + 8 * s * ss), int(block_top + (name_line - badge) / 2)))
    d.text((name_x, block_top + name_line + (handle_line - sum(handle_font.getmetrics())) / 2 + handle_font.getmetrics()[0]),
           "@" + handle, font=handle_font, fill=colours["handle"] + (255,), anchor="ls")

    y = top + head_h + round(40 * s) * ss
    for line in lines:
        draw_pieces(layer, d, left, y, line, fnt, size, line_h)
        y += line_h

    if ss > 1:
        layer = layer.resize((card_w, card_h // ss), Image.Resampling.LANCZOS)
    card_h //= ss
    y0 = {"top": round(H * 0.07), "middle": (H - card_h) // 2, "bottom": H - card_h - round(H * 0.11)}[position]
    y0 = max(round(H * 0.02), min(y0, H - card_h - round(H * 0.02)))
    canvas = photo.convert("RGBA")
    canvas.alpha_composite(layer, (margin_x, y0))
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0, optimize=True)
    return out_path