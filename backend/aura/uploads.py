"""Uploads: type from magic bytes (never the extension), size caps, an id, a delete token.

Files go to <DATA_DIR>/uploads/<id>/source.<ext>; the client filename is never used for paths.
DXF, PDF and IFC are open to everyone; PNG/JPG need the presenter token unless
AURA_RASTER_PUBLIC_CAP allows them (0 = unlimited, the default). DWG and zip archives are rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

MB = 1024 * 1024
BODY_CAP = 65 * MB
CAPS = {"dxf": 20 * MB, "png": 15 * MB, "jpg": 15 * MB, "pdf": 40 * MB, "ifc": 60 * MB}
MAX_PIXELS = 40_000_000
TTL_S = 24 * 3600


class UploadError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def sniff(head: bytes) -> str | None:
    """'dxf' | 'png' | 'jpg' | 'pdf' | 'ifc' | 'dwg' | 'zip' | None, from the first bytes."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head[:4] in (b"AC10", b"AC1.") or head.startswith(b"AC1"):
        return "dwg"
    if head.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00"):
        return "dxf"
    text = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if text.startswith(b"ISO-10303-21;"):
        return "ifc"
    # ASCII DXF: group code 0, then SECTION (optionally preceded by 999 comments).
    lines = [ln.strip() for ln in text.split(b"\n")[:8]]
    while len(lines) >= 2 and lines[0] == b"999":
        lines = lines[2:]
    if len(lines) >= 2 and lines[0] == b"0" and lines[1].upper() == b"SECTION":
        return "dxf"
    return None


def image_size(head: bytes, kind: str) -> tuple[int, int] | None:
    """Pixel size from the header alone (no decode)."""
    if kind == "png" and len(head) >= 24 and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        return w, h
    if kind == "jpg":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                return None
            marker = head[i + 1]
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE,
                          0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            i += 2 + seg
    return None


def new_id() -> str:
    return "up-" + secrets.token_urlsafe(16).replace("_", "x").replace("-", "y")


def new_delete_token() -> str:
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_ok(given: str | None, stored_hash: str | None) -> bool:
    if not given or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(given), stored_hash)


@dataclass
class Accepted:
    drawing_id: str
    kind: str  # dxf | png | jpg
    dir: Path
    src: Path
    delete_token: str


def validate(head: bytes, size: int, *, presenter_ok: bool) -> str:
    """Raises UploadError; returns the sniffed kind."""
    kind = sniff(head)
    if kind in (None, "dwg", "zip"):
        what = {"dwg": "DWG files", "zip": "Archives"}.get(kind or "", "This file type")
        raise UploadError(
            "unsupported_type", f"{what} are not accepted. Upload a DXF, PDF, or IFC.", 415
        )
    if size > CAPS[kind]:
        raise UploadError("too_large", f"{kind.upper()} uploads are limited to "
                                       f"{CAPS[kind] // MB} MB.", 413)
    if kind in ("png", "jpg"):
        dims = image_size(head, kind)
        if dims is None:
            raise UploadError("unsupported_type", "Could not read the image header.", 415)
        if dims[0] * dims[1] > MAX_PIXELS:
            raise UploadError("too_large", "Images are limited to 40 megapixels.", 413)
        if not presenter_ok:
            raise UploadError("presenter_required",
                              "Image uploads need the presenter token; DXF, PDF and IFC are open.", 403)
    return kind


def store(uploads_dir: Path, kind: str, data_path: Path) -> Accepted:
    """Move the received temp file under a fresh id. The client filename is never used."""
    did = new_id()
    d = uploads_dir / did
    d.mkdir(parents=True, exist_ok=False)
    ext = "dxf" if kind == "dxf" else kind
    src = d / f"source.{ext}"
    data_path.replace(src)
    return Accepted(drawing_id=did, kind=kind, dir=d, src=src, delete_token=new_delete_token())
