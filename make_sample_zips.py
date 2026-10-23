#!/usr/bin/env python3
"""Creates test uploads in sample_data/ : cat.jpg, bulk_20.zip, bulk_60.zip, mixed.zip, corrupt.zip, empty.zip.

    python make_sample_zips.py
Downloads 8 public COCO photos once; falls back to generated images if offline.
"""
import io
import pathlib
import urllib.request
import zipfile

from PIL import Image

OUT = pathlib.Path(__file__).with_name("sample_data")
OUT.mkdir(exist_ok=True)
COCO = "http://images.cocodataset.org/val2017/{:012d}.jpg"
IDS = [39769, 139, 285, 632, 724, 776, 785, 872]

photos = []
for i in IDS:
    try:
        photos.append(urllib.request.urlopen(COCO.format(i), timeout=20).read())
    except Exception:
        img = Image.new("RGB", (640, 480), (i % 255, (i * 7) % 255, (i * 13) % 255))
        buf = io.BytesIO(); img.save(buf, "JPEG"); photos.append(buf.getvalue())
(OUT / "cat.jpg").write_bytes(photos[0])


def make_zip(name, count=0, extra=()):
    with zipfile.ZipFile(OUT / name, "w", zipfile.ZIP_DEFLATED) as z:
        for n in range(count):
            z.writestr(f"img_{n:03d}.jpg", photos[n % len(photos)])
        for fname, data in extra:
            z.writestr(fname, data)


make_zip("bulk_20.zip", 20)
make_zip("bulk_60.zip", 60)
make_zip("mixed.zip", 4, [("notes.txt", b"not an image"), ("broken.jpg", b"this is not a jpeg"), ("folder/", b"")])
make_zip("empty.zip", 0, [("readme.txt", b"no images here")])
(OUT / "corrupt.zip").write_bytes(b"this is definitely not a zip archive")
print("created:", ", ".join(sorted(p.name for p in OUT.iterdir())))
