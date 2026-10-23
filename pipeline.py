"""Model + single/bulk processing, with all instrumentation. No Streamlit imports -> easy to test."""
import io
import os
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional

import psutil
from PIL import Image, UnidentifiedImageError

import metrics as m

MODEL_ID = "Salesforce/blip-image-captioning-base"                # CPU-friendly BLIP
# one torch thread per PHYSICAL core: using all logical (hyper-threaded) cores was ~4x slower in benchmarks
NUM_THREADS = int(os.getenv("CAPTION_THREADS", str(psutil.cpu_count(logical=False) or 4)))
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
MAX_ZIP_MB = int(os.getenv("MAX_ZIP_MB", "200"))                  # compressed upload cap
MAX_UNZIPPED_MB = int(os.getenv("MAX_UNZIPPED_MB", "500"))        # zip-bomb guard
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "100"))                  # images captioned per ZIP
MAX_SIDE = 1024                                                   # downscale before the model

# One inference at a time: two concurrent users must not oversubscribe the CPU or double the RAM.
INFERENCE_LOCK = threading.Lock()


@dataclass
class Client:
    ip: str
    session_id: str
    browser: str


@dataclass
class Result:
    filename: str
    status: str                  # success | error
    caption: str = ""
    seconds: float = 0.0
    error: str = ""


def load_model():
    """Load BLIP once (caller caches it) and publish the model gauges."""
    import torch
    from transformers import BlipForConditionalGeneration, BlipProcessor
    started = time.perf_counter()
    rss_before = psutil.Process().memory_info().rss
    torch.set_num_threads(NUM_THREADS)
    processor = BlipProcessor.from_pretrained(MODEL_ID)
    model = BlipForConditionalGeneration.from_pretrained(MODEL_ID).eval()
    m.MODEL_LOAD_SECONDS.set(time.perf_counter() - started)
    m.MODEL_MEMORY.set(max(psutil.Process().memory_info().rss - rss_before, 0))
    m.TORCH_THREADS.set(NUM_THREADS)
    m.MODEL_LOADED.set(1)
    m.BUILD_INFO.info({"model": MODEL_ID, "device": "cpu", "version": "1.0.0", "course": "DA3408-A4"})
    return processor, model


def caption(bundle, image) -> str:
    """Caption one PIL RGB image. Caller holds INFERENCE_LOCK."""
    import torch
    processor, model = bundle
    inputs = processor(images=image, return_tensors="pt")
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=30, num_beams=3)
    return processor.decode(out[0], skip_special_tokens=True).strip()


def _caption_bytes(bundle, data: bytes, name: str, mode: str, client: Client) -> Result:
    """Decode one image, caption it, update every per-image metric."""
    m.IMAGE_BYTES.labels(mode).observe(len(data))
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        m.IMAGES.labels(mode, "error").inc()
        m.ERRORS.labels(mode, "invalid_image").inc()
        return Result(name, "error", error=f"invalid image: {exc.__class__.__name__}")

    m.WAITING.labels(mode).inc()
    with INFERENCE_LOCK:
        m.WAITING.labels(mode).dec()
        started = time.perf_counter()
        try:
            text = caption(bundle, img)
        except Exception as exc:                                   # keep the app alive on any model failure
            m.IMAGES.labels(mode, "error").inc()
            m.ERRORS.labels(mode, "inference_error").inc()
            return Result(name, "error", error=f"inference failed: {exc.__class__.__name__}")
        seconds = time.perf_counter() - started

    m.INFERENCE_SECONDS.labels(mode).observe(seconds)
    m.CLIENT_INFERENCE.labels(mode, client.ip).observe(seconds)
    m.LAST_INFERENCE.labels(mode).set(seconds)
    m.CAPTION_CHARS.labels(mode).observe(len(text))
    m.CAPTION_WORDS.labels(mode).inc(len(text.split()))
    m.IMAGES.labels(mode, "success").inc()
    m.CLIENT_IMAGES.labels(mode, client.ip, client.session_id).inc()
    return Result(name, "success", caption=text, seconds=seconds)


def process_single(bundle, data: bytes, name: str, client: Client) -> Result:
    mode = "single"
    m.CLIENT_REQUESTS.labels(mode, client.ip, client.browser).inc()
    m.UPLOAD_BYTES.labels(mode).observe(len(data))
    m.UPLOADED_BYTES.labels(mode).inc(len(data))
    started = time.perf_counter()
    with m.UNHANDLED.labels(mode).count_exceptions(), m.ACTIVE_REQUESTS.labels(mode).track_inprogress():
        result = _caption_bytes(bundle, data, name, mode, client)
    m.REQUESTS.labels(mode, "ok" if result.status == "success" else "error").inc()
    m.REQUEST_SECONDS.labels(mode).observe(time.perf_counter() - started)
    return result


def _list_images(zf: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    keep = []
    for info in zf.infolist():
        base = os.path.basename(info.filename)
        if info.is_dir():
            m.ZIP_SKIPPED.labels("directory").inc()
        elif base.startswith(".") or "__MACOSX" in info.filename:
            m.ZIP_SKIPPED.labels("hidden").inc()
        elif not base.lower().endswith(IMAGE_EXTS):
            m.ZIP_SKIPPED.labels("not_an_image").inc()
        else:
            keep.append(info)
    if len(keep) > MAX_IMAGES:
        m.ZIP_SKIPPED.labels("over_image_cap").inc(len(keep) - MAX_IMAGES)
        keep = keep[:MAX_IMAGES]
    return keep


def process_zip(bundle, data: bytes, client: Client,
                on_progress: Optional[Callable[[int, int, Result], None]] = None) -> List[Result]:
    mode = "bulk"
    m.CLIENT_REQUESTS.labels(mode, client.ip, client.browser).inc()
    m.UPLOAD_BYTES.labels(mode).observe(len(data))
    m.UPLOADED_BYTES.labels(mode).inc(len(data))
    started = time.perf_counter()
    results: List[Result] = []

    with m.UNHANDLED.labels(mode).count_exceptions(), m.ACTIVE_REQUESTS.labels(mode).track_inprogress():
        try:
            if len(data) > MAX_ZIP_MB * 1024 * 1024:
                raise ValueError("zip_too_large")
            zf = zipfile.ZipFile(io.BytesIO(data))
            if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED_MB * 1024 * 1024:
                raise ValueError("zip_too_large")
            entries = _list_images(zf)
            if not entries:
                raise ValueError("zip_empty")
        except zipfile.BadZipFile:
            m.ERRORS.labels(mode, "zip_invalid").inc()
            m.REQUESTS.labels(mode, "error").inc()
            return [Result("(upload)", "error", error="not a valid ZIP file")]
        except ValueError as exc:
            m.ERRORS.labels(mode, str(exc)).inc()
            m.REQUESTS.labels(mode, "error").inc()
            return [Result("(upload)", "error", error=str(exc).replace("_", " "))]

        total = len(entries)
        m.BATCH_IMAGES.observe(total)
        m.BATCH_SIZE_LAST.set(total)
        m.BATCH_REMAINING.set(total)
        m.BATCH_PROGRESS.set(0)
        for i, info in enumerate(entries, start=1):
            try:
                raw = zf.read(info)
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile, OSError, EOFError):
                m.IMAGES.labels(mode, "error").inc()                  # encrypted / corrupt entry
                m.ERRORS.labels(mode, "unreadable_entry").inc()
                res = Result(info.filename, "error", error="unreadable ZIP entry")
            else:
                res = _caption_bytes(bundle, raw, info.filename, mode, client)
            results.append(res)
            m.BATCH_REMAINING.set(total - i)
            m.BATCH_PROGRESS.set(i / total)
            if on_progress:
                on_progress(i, total, res)

    ok = any(r.status == "success" for r in results)
    m.REQUESTS.labels(mode, "ok" if ok else "error").inc()
    m.REQUEST_SECONDS.labels(mode).observe(time.perf_counter() - started)
    return results
