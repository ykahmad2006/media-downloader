"""
Instagram: posts, carousels, reels and stories. Needs fetchly_core.py next to it.
Stories and private posts need COOKIES_BROWSER set in fetchly_core.py (your own logged-in browser).
"""

import os
import uuid

from fetchly_core import fetch

url = input("Paste an Instagram link (post, reel or story): ").strip()
folder = os.path.join("downloads", uuid.uuid4().hex[:8])
os.makedirs(folder)

try:
    path = fetch(url, "gallery", folder)       # photo posts become an MP4 slideshow, mixed posts a ZIP
except Exception:
    try:
        path = fetch(url, "v4320", folder)     # fallback: best single video
    except Exception as error:
        raise SystemExit(f"Download FAILED: {error}")
print("Saved:", os.path.abspath(path))