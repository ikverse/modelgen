"""Reference images the person attaches in the chat or on the New model screen.

Saved under data\\uploads as PNG or JPEG (other formats are converted), named by a random id.
The agents see them through the view_reference tool; the page shows them from /uploads/<file>.
"""

import base64
import binascii
import io
import re
import secrets

from PIL import Image

from . import config

MAX_BYTES = 12 * 1024 * 1024
MAX_SIDE = 2048
ID = re.compile(r"^[a-f0-9]{16}\.(png|jpg)$")


def folder():
    return config.data_dir() / "uploads"


def save(data_url):
    """Store an image sent as a data URL. Returns {"id", "url"}; raises ValueError if it is not one."""
    match = re.match(r"^data:image/[a-z0-9.+-]+;base64,(.+)$", data_url or "", re.S)
    if not match:
        raise ValueError("send the image as a base64 data URL")
    try:
        raw = base64.b64decode(match.group(1), validate=False)
    except (binascii.Error, ValueError):
        raise ValueError("the image data is not valid base64")
    if len(raw) > MAX_BYTES:
        raise ValueError("the image is larger than 12 MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise ValueError("that file is not an image")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    keep_alpha = img.mode in ("RGBA", "LA", "P")
    ext = "png" if keep_alpha else "jpg"
    name = f"{secrets.token_hex(8)}.{ext}"
    folder().mkdir(parents=True, exist_ok=True)
    if keep_alpha:
        img.convert("RGBA").save(folder() / name, "PNG")
    else:
        img.convert("RGB").save(folder() / name, "JPEG", quality=90)
    return {"id": name, "url": f"/uploads/{name}"}


def path(upload_id):
    """The file of an upload id, or None if the id is not a stored upload."""
    if not ID.match(upload_id or ""):
        return None
    p = folder() / upload_id
    return p if p.is_file() else None


def url(upload_id):
    return f"/uploads/{upload_id}"
