"""Prometheus metrics for the captioning app. Everything is defined here; the rest of the code only calls
.inc() / .observe() / .set()."""
import threading
import time

import psutil
from prometheus_client import (Counter, Gauge, Histogram, Info, Summary,
                               disable_created_metrics, start_http_server)

disable_created_metrics()      # drop the *_created series, as in the class examples

METRICS_PORT = 18000
P = "captioner"

# Counters
REQUESTS = Counter(f"{P}_requests_total", "Requests handled (one image or one ZIP)", ["mode", "status"])
IMAGES = Counter(f"{P}_images_processed_total", "Images processed", ["mode", "status"])
ERRORS = Counter(f"{P}_errors_total", "Errors by type", ["mode", "error_type"])
UNHANDLED = Counter(f"{P}_unhandled_exceptions_total", "Exceptions that escaped a handler", ["mode"])
CAPTION_WORDS = Counter(f"{P}_caption_words_total", "Words generated", ["mode"])
UPLOADED_BYTES = Counter(f"{P}_uploaded_bytes_total", "Bytes uploaded", ["mode"])
ZIP_SKIPPED = Counter(f"{P}_zip_entries_skipped_total", "ZIP entries ignored", ["reason"])
CLIENT_IMAGES = Counter(f"{P}_client_images_total", "Images captioned per client", ["mode", "client_ip", "session_id"])
CLIENT_REQUESTS = Counter(f"{P}_client_requests_total", "Requests per client", ["mode", "client_ip", "browser"])

# Gauges
ACTIVE_REQUESTS = Gauge(f"{P}_active_requests", "Requests in progress", ["mode"])
WAITING = Gauge(f"{P}_waiting_for_model", "Images waiting for the model", ["mode"])
MODEL_LOADED = Gauge(f"{P}_model_loaded", "1 when the model is loaded")
MODEL_MEMORY = Gauge(f"{P}_model_memory_bytes", "RAM growth from loading the model")
MODEL_LOAD_SECONDS = Gauge(f"{P}_model_load_seconds", "Model load time")
TORCH_THREADS = Gauge(f"{P}_torch_threads", "CPU threads used by torch")
LAST_INFERENCE = Gauge(f"{P}_last_inference_seconds", "Latest inference time", ["mode"])
BATCH_SIZE_LAST = Gauge(f"{P}_batch_size_last", "Images in the latest ZIP")
BATCH_REMAINING = Gauge(f"{P}_batch_images_remaining", "Images left in the running ZIP")
BATCH_PROGRESS = Gauge(f"{P}_batch_progress_ratio", "Progress of the running ZIP (0-1)")
PROCESS_MEMORY = Gauge(f"{P}_process_memory_bytes", "Resident memory of the app")
PROCESS_MEMORY.set_function(lambda: psutil.Process().memory_info().rss)

_sessions = {}                 # session id -> last seen
_lock = threading.Lock()


def touch_session(session_id: str) -> None:
    with _lock:
        _sessions[session_id] = time.time()


def _recent_sessions() -> int:
    cutoff = time.time() - 300
    with _lock:
        for sid in [s for s, t in _sessions.items() if t < cutoff]:
            del _sessions[sid]
        return len(_sessions)


Gauge(f"{P}_recent_sessions", "Sessions seen in the last 5 minutes").set_function(_recent_sessions)

# Histograms
INFERENCE_SECONDS = Histogram(f"{P}_inference_duration_seconds", "Inference time per image", ["mode"],
                              buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 7.5, 10, 15, 30, 60))
REQUEST_SECONDS = Histogram(f"{P}_request_duration_seconds", "Time for a whole request", ["mode"],
                            buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600))
IMAGE_BYTES = Histogram(f"{P}_image_size_bytes", "Image size", ["mode"],
                        buckets=(1e4, 5e4, 1e5, 2.5e5, 5e5, 1e6, 2.5e6, 5e6, 1e7, 2.5e7))
BATCH_IMAGES = Histogram(f"{P}_batch_images", "Images per ZIP", buckets=(1, 5, 10, 20, 50, 100, 200))

# Summaries (exposed as _sum and _count)
CAPTION_CHARS = Summary(f"{P}_caption_length_chars", "Characters per caption", ["mode"])
UPLOAD_BYTES = Summary(f"{P}_upload_size_bytes", "Upload size", ["mode"])
CLIENT_INFERENCE = Summary(f"{P}_client_inference_seconds", "Inference time per client", ["mode", "client_ip"])

BUILD_INFO = Info(f"{P}_build", "Model and build information")

# Create the known label combinations at 0 so rate() sees the first event and panels never start empty.
for mode in ("single", "bulk"):
    for g in (ACTIVE_REQUESTS, WAITING):
        g.labels(mode).set(0)
    for m in (UNHANDLED, CAPTION_WORDS, UPLOADED_BYTES, INFERENCE_SECONDS, REQUEST_SECONDS,
              IMAGE_BYTES, CAPTION_CHARS, UPLOAD_BYTES):
        m.labels(mode)
    for status in ("ok", "error"):
        REQUESTS.labels(mode, status)
    for status in ("success", "error"):
        IMAGES.labels(mode, status)
    for err in ("invalid_image", "inference_error"):
        ERRORS.labels(mode, err)
for err in ("zip_invalid", "zip_empty", "zip_too_large", "unreadable_entry"):
    ERRORS.labels("bulk", err)
for reason in ("directory", "hidden", "not_an_image", "over_image_cap"):
    ZIP_SKIPPED.labels(reason)

_started = False


def start_metrics_server(port: int = METRICS_PORT) -> None:
    """Expose /metrics on its own port (once, because Streamlit re-runs the script on every click)."""
    global _started
    with _lock:
        if not _started:
            start_http_server(port)
            _started = True


def browser_family(user_agent: str) -> str:
    """Reduce the User-Agent to a few values to keep label cardinality low."""
    ua = (user_agent or "").lower()
    for token, name in (("edg/", "edge"), ("firefox", "firefox"), ("chrome", "chrome"),
                        ("safari", "safari"), ("curl", "curl"), ("python", "python")):
        if token in ua:
            return name
    return "other"
