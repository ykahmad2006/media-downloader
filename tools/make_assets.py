"""
Makes the picture people see when the site's link is shared (static/og-image.jpg, 1200x630) and the site icons.
Run from the fetchly folder:  python tools/make_assets.py
Needs Pillow. Uses the Inter font in fonts/ (SIL Open Font License).
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FONT = os.path.join(ROOT, "fonts", "Inter-Variable.ttf")
OUT = os.path.join(ROOT, "static")
NAVY, CREAM, CORAL = (20, 33, 61), (244, 237, 224), (255, 107, 91)


def inter(size, weight):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_axes([14, weight])
    return f


def glow(canvas, cx, cy, radius, colour, strength):
    layer = Image.new("RGBA", canvas.size, colour + (0,))
    mask = Image.radial_gradient("L").resize((radius * 2, radius * 2), Image.Resampling.BICUBIC)
    # the gradient reaches white at the square's corners, so it is about 180 at the edge midpoints: fade to nothing there
    mask = mask.point(lambda v: int(255 * max(0.0, 1 - v / 180.0) ** 2 * strength))
    layer.paste(Image.new("RGBA", mask.size, colour + (255,)), (cx - radius, cy - radius), mask)
    canvas.alpha_composite(layer)


def og_image():
    W, H = 1200, 630
    img = Image.new("RGBA", (W, H), NAVY + (255,))
    glow(img, 880, 640, 560, CORAL, 0.95)                    # the warm glow, low on the right
    glow(img, 140, 60, 480, (58, 84, 160), 0.7)               # a cool haze, high on the left
    ribs = Image.new("RGBA", (W, H), (0, 0, 0, 0))            # fluted-glass stripes, very faint
    d = ImageDraw.Draw(ribs)
    for x in range(0, W, 7):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 9), width=2)
    img.alpha_composite(ribs)
    d = ImageDraw.Draw(img)
    d.text((72, 62), "fetch", font=inter(46, 700), fill=CREAM)
    d.text((72 + d.textlength("fetch", font=inter(46, 700)), 62), "ly", font=inter(46, 700), fill=CORAL)
    for i, line in enumerate(("Save any video,", "photo or tweet", "with just its link.")):
        d.text((72, 170 + i * 92), line, font=inter(78, 300), fill=CREAM)
    d.text((72, 470), "YouTube  ·  X  ·  TikTok  ·  Instagram", font=inter(29, 500), fill=(244, 237, 224, 190))
    pill = (72, 528, 372, 592)
    d.rounded_rectangle(pill, radius=32, fill=CORAL)
    d.text(((pill[0] + pill[2]) / 2, (pill[1] + pill[3]) / 2 + 1), "Paste a link  ↗", font=inter(27, 700), fill=NAVY, anchor="mm")
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))            # a glass card with the three steps
    c = ImageDraw.Draw(card)
    box = (770, 150, 1130, 470)
    c.rounded_rectangle(box, radius=34, fill=(255, 255, 255, 30), outline=(255, 255, 255, 70), width=2)
    for i, (n, title, hint) in enumerate((("1", "Paste the link", "any public post"), ("2", "Pick a size", "best quality first"), ("3", "Save the file", "no sign-up"))):
        y = box[1] + 34 + i * 96
        c.ellipse([box[0] + 28, y, box[0] + 72, y + 44], fill=CORAL)
        c.text((box[0] + 50, y + 23), n, font=inter(24, 700), fill=NAVY, anchor="mm")
        c.text((box[0] + 92, y - 2), title, font=inter(27, 600), fill=CREAM)
        c.text((box[0] + 92, y + 32), hint, font=inter(20, 400), fill=(244, 237, 224, 160))
    img.alpha_composite(card)
    os.makedirs(OUT, exist_ok=True)
    img.convert("RGB").save(os.path.join(OUT, "og-image.jpg"), "JPEG", quality=88, optimize=True, progressive=True)


def icons():
    big = 512
    base = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=112, fill=NAVY)
    glow(base, 360, 500, 300, CORAL, 0.8)
    d = ImageDraw.Draw(base)
    stroke = 46                                                # a download arrow in coral
    d.line([(256, 118), (256, 322)], fill=CORAL, width=stroke)
    d.line([(168, 238), (256, 326), (344, 238)], fill=CORAL, width=stroke, joint="curve")
    d.line([(150, 396), (362, 396)], fill=CREAM, width=stroke)
    for x, y in ((256, 118), (150, 396), (362, 396)):
        d.ellipse([x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2], fill=CORAL if y < 300 else CREAM)
    base.resize((180, 180), Image.Resampling.LANCZOS).save(os.path.join(OUT, "apple-touch-icon.png"), optimize=True)
    base.resize((192, 192), Image.Resampling.LANCZOS).save(os.path.join(OUT, "icon-192.png"), optimize=True)
    base.save(os.path.join(OUT, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    with open(os.path.join(OUT, "favicon.svg"), "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#14213D"/>'
                '<path d="M32 14v26M21 30l11 11 11-11" stroke="#FF6B5B" stroke-width="5.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
                '<path d="M19 50h26" stroke="#F4EDE0" stroke-width="5.5" stroke-linecap="round"/></svg>')


if __name__ == "__main__":
    og_image()
    icons()
    for name in sorted(os.listdir(OUT)):
        path = os.path.join(OUT, name)
        if os.path.isfile(path):
            print(f"{name:<22}{os.path.getsize(path) // 1024:>5} KB")
