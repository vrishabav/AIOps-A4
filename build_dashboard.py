"""Generates dashboard.json (the Grafana dashboard export):  python build_dashboard.py

Every panel is built by the helpers below so the 7 plotting commandments hold uniformly
(colour-blind palette, line styles, axis labels with unit and scale, legend, title + subtitle, story).
Prometheus scrapes every 1 s, so rate() uses [15s] windows and [1m] for percentiles and ratios.

AI use: the PromQL below was drafted with Claude Code. Prompts used (one per panel group):
  1  "PromQL for the number of Prometheus alerts firing right now, 0 instead of 'no data' when none."
  2  "PromQL for the fraction of images that failed over the last minute; avoid divide-by-zero, show 0 when idle."
  3  "PromQL for images per second by mode from a counter, 15s window, only successes."
  4  "PromQL p50, p95 and p99 inference latency from a Prometheus histogram, 1 minute window."
  5  "PromQL mean latency per mode from a Summary's _sum and _count, 1 minute window."
  6  "PromQL error rate per type from a counter; group all zip-related error types with a regex."
  7  "PromQL table of images per client_ip and mode, and mean latency per client from a Summary."
  8  "PromQL request rate per browser label from a counter, 15s window."
  9  "PromQL total CPU busy percent from node_exporter idle seconds, 15s window, and per-mode percent."
  10 "PromQL RAM used percent from MemAvailable/MemTotal, root filesystem used percent, disk read/write bytes per second."
  11 "PromQL load1 divided by the number of CPU cores; network receive/transmit bytes per second excluding lo, docker, veth."
  12 "PromQL for a python process's CPU as percent of one core from process_cpu_seconds_total."
  13 "Grafana annotation query marking when any Prometheus alert is firing."
"""
import json
import pathlib

# Colour-blind-safe palette (Okabe & Ito)
BLUE, ORANGE, SKY, VERM = "#0072B2", "#E69F00", "#56B4E9", "#D55E00"
GREEN, PURPLE, BLACK, GREY = "#009E73", "#CC79A7", "#000000", "#999999"

# Marker / line styles that still differ in greyscale print: solid, dash, dot, dash-dot
SOLID = {"fill": "solid"}
DASH = {"fill": "dash", "dash": [10, 10]}
DOT = {"fill": "dot", "dash": [2, 6]}
DASHDOT = {"fill": "dash", "dash": [12, 6, 2, 6]}

DS = {"type": "prometheus", "uid": "${ds}"}
_next_id = [0]


def _id():
    _next_id[0] += 1
    return _next_id[0]


def _target(ref, expr, legend="", table=False):
    t = {"refId": ref, "datasource": DS, "expr": expr, "legendFormat": legend or "__auto",
         "editorMode": "code", "range": not table, "instant": table}
    if table:
        t["format"] = "table"
    return t


def desc(subtitle, story, unit_scale):
    return f"{subtitle}\n\nSTORY: {story}\n\nSCALE/UNITS: {unit_scale}"


def text_panel(title, md, x, y, w, h):
    return {"id": _id(), "type": "text", "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": md}, "transparent": True}


def row(title, y):
    return {"id": _id(), "type": "row", "title": title, "collapsed": False, "panels": [],
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}}


def stat(title, subtitle, story, expr, x, y, w, h=3, unit="none", mappings=None, steps=None,
         decimals=None, no_value="NO DATA", unit_scale="n/a (single value)"):
    steps = steps or [{"color": BLUE, "value": None}]
    d = {"unit": unit, "mappings": mappings or [], "noValue": no_value,
         "color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": steps}}
    if decimals is not None:
        d["decimals"] = decimals
    return {"id": _id(), "type": "stat", "title": f"{title} · {subtitle}",
            "description": desc(subtitle, story, unit_scale), "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [_target("A", expr)],
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none", "textMode": "value",
                        "justifyMode": "center", "orientation": "auto"}}


def updown(label_up, label_down):
    """Value mapping so the state is also readable as TEXT, not only colour (colour-blind safe)."""
    return [{"type": "value", "options": {
        "1": {"text": label_up, "color": BLUE, "index": 0},
        "0": {"text": label_down, "color": VERM, "index": 1}}}]


def timeseries(title, subtitle, story, series, x, y, w, h, unit, y_label, scale="linear",
               unit_scale="", ymin=None, ymax=None, right=None):
    """series = [(refId, promql, legend, colour, line_style, point_size)]
    right = None | dict(refs=[...], unit=..., label=..., min=..., max=...) -> second Y axis."""
    custom = {"drawStyle": "line", "lineWidth": 2, "lineInterpolation": "linear", "showPoints": "always",
              "pointSize": 4, "fillOpacity": 0, "axisPlacement": "left", "axisLabel": f"{y_label} ({scale})",
              "axisColorMode": "text", "spanNulls": False, "stacking": {"mode": "none", "group": "A"},
              "scaleDistribution": {"type": "log", "log": 10} if scale == "log" else {"type": "linear"},
              "thresholdsStyle": {"mode": "off"}}
    if scale == "linear":
        custom["axisSoftMin"] = 0
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "fixed", "fixedColor": BLUE},
                "thresholds": {"mode": "absolute", "steps": [{"color": BLUE, "value": None}]}}
    if not right:                                   # with two axes, limits go on each series (below)
        if ymin is not None:
            defaults["min"] = ymin
        if ymax is not None:
            defaults["max"] = ymax
    overrides, targets = [], []
    for ref, expr, legend, colour, style, psize in series:
        targets.append(_target(ref, expr, legend))
        props = [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}},
                 {"id": "custom.lineStyle", "value": style},
                 {"id": "custom.pointSize", "value": psize}]
        if right and ref not in right["refs"]:      # left-axis series of a dual-axis panel
            props += [{"id": "min", "value": ymin}] if ymin is not None else []
            props += [{"id": "max", "value": ymax}] if ymax is not None else []
        if right and ref in right["refs"]:
            props += [{"id": "custom.axisPlacement", "value": "right"},
                      {"id": "unit", "value": right["unit"]},
                      {"id": "custom.axisLabel", "value": f"{right['label']} ({right.get('scale', 'linear')})"}]
            if "min" in right:
                props.append({"id": "min", "value": right["min"]})
            if "max" in right:
                props.append({"id": "max", "value": right["max"]})
        overrides.append({"matcher": {"id": "byFrameRefID", "options": ref}, "properties": props})
    return {"id": _id(), "type": "timeseries", "title": f"{title} · {subtitle}",
            "description": desc(subtitle, story, unit_scale or f"{y_label}; {scale} Y axis; X axis = time (local)."),
            "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": overrides},
            "options": {"legend": {"showLegend": True, "displayMode": "table", "placement": "bottom",
                                   "calcs": ["lastNotNull", "max"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def bargauge(title, subtitle, story, expr, legend, x, y, w, h, unit="none", unit_scale="linear bar length"):
    return {"id": _id(), "type": "bargauge", "title": f"{title} · {subtitle}",
            "description": desc(subtitle, story, unit_scale), "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [dict(_target("A", expr, legend, table=False), instant=True, range=False)],
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "color": {"mode": "fixed", "fixedColor": BLUE},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": BLUE, "value": None}]}},
                            "overrides": []},
            "options": {"displayMode": "basic", "orientation": "horizontal", "showUnfilled": True,
                        "valueMode": "text", "namePlacement": "top", "sizing": "manual", "minVizHeight": 24, "maxVizHeight": 60,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


panels = []
y = 0

panels.append(text_panel(
    "How to read this dashboard",
    "**DA3408 A4 · AI image captioner (BLIP, CPU-only).** Read top to bottom: **1 Health** (is it up?) → "
    "**2 Application** (throughput · latency · errors) → **3 Clients** (who is calling) → **4 System** "
    "(node_exporter: CPU / RAM / disk) → **5 Correlation** (does a bulk upload move the hardware?).  \n"
    "Colours are the colour-blind-safe Okabe-Ito palette; series also differ by **line style** "
    "(solid / dashed / dotted / dash-dot) so they survive greyscale print. Every panel has a title, "
    "subtitle, axis label with unit + scale (linear/log) and a story in its ⓘ description. Refresh: 5 s.",
    0, y, 24, 5))
y += 5

# Row 1 : HEALTH
panels.append(row("1 · Health at a glance", y)); y += 1
S = 6  # 4 tiles per row, 2 rows (titles stay readable)
panels += [
    stat("App", "UP / DOWN", "Is Prometheus able to scrape the Streamlit app's /metrics? DOWN = app crashed or stopped.",
         'up{job="python_app"}', 0 * S, y, S, mappings=updown("UP", "DOWN")),
    stat("node_exporter", "UP / DOWN", "System metrics source. DOWN = CPU/RAM/disk panels below go blank.",
         'up{job="node"}', 1 * S, y, S, mappings=updown("UP", "DOWN")),

    stat("Alerts", "firing now", "Count of alert rules currently firing (see rules.yml). 0 = healthy.",
         'count(ALERTS{alertstate="firing"}) or vector(0)', 2 * S, y, S, decimals=0,
         steps=[{"color": BLUE, "value": None}, {"color": VERM, "value": 1}]),
    stat("Model", "LOADED / NOT", "BLIP weights resident in RAM. NOT LOADED = first request will be slow.",
         'captioner_model_loaded', 3 * S, y, S, mappings=updown("LOADED", "NOT LOADED")),
    stat("Active requests", "in progress now", "Uploads currently being processed (single + bulk).",
         'sum(captioner_active_requests)', 0 * S, y + 3, S, decimals=0,
         steps=[{"color": BLUE, "value": None}, {"color": ORANGE, "value": 2}, {"color": VERM, "value": 4}]),
    stat("Sessions", "browser tabs, last 5 min", "Distinct Streamlit sessions seen in the last 5 minutes.",
         'captioner_recent_sessions', 1 * S, y + 3, S, decimals=0),

    stat("Image error ratio", "last 1 min", "Failed images / all images over 1 min. Orange > 5 %, vermilion > 20 % (alert threshold).",
         '(sum(rate(captioner_images_processed_total{status="error"}[1m])) '
         '/ (sum(rate(captioner_images_processed_total[1m])) > 0)) or vector(0)',
         2 * S, y + 3, S, unit="percentunit", decimals=1,
         steps=[{"color": BLUE, "value": None}, {"color": ORANGE, "value": 0.05}, {"color": VERM, "value": 0.2}],
         unit_scale="percent, 0-100 % (linear)"),
    stat("Model memory", "RAM used by weights", "RSS growth when the model was loaded - the model's own footprint.",
         'captioner_model_memory_bytes', 3 * S, y + 3, S, unit="bytes", decimals=1,
         unit_scale="bytes (IEC), single value"),
]
y += 6

# Row 2 : APPLICATION (RED)
panels.append(row("2 · Application — Rate · Errors · Duration", y)); y += 1
panels.append(text_panel(
    "Story", "Each image is one unit of work. **Throughput** shows how many are finished per second, "
    "**latency** how long each takes (log scale because bulk bursts spread values over decades), "
    "**errors** what is failing and **queue** how many images wait for the single model.", 0, y, 24, 3))
y += 3
panels.append(timeseries(
    "Throughput", "images per second by mode",
    "Bulk uploads show as a plateau (steady CPU-bound rate); single uploads as short spikes.",
    [
        ("A", 'sum(rate(captioner_images_processed_total{mode="single",status="success"}[15s]))', "Single", BLUE, SOLID, 4),
        ("B", 'sum(rate(captioner_images_processed_total{mode="bulk",status="success"}[15s]))', "Bulk", ORANGE, DASH, 7)],
    0, y, 12, 8, "suffix: img/s", "Images/s", ymin=0))
panels.append(timeseries(
    "Inference latency", "p50/p95/p99, log scale",
    "p50 is the typical image; a p95/p99 that pulls away from p50 means queueing or oversized images.",
    [
        ("A", 'histogram_quantile(0.50, sum by (le) (rate(captioner_inference_duration_seconds_bucket[1m])))', "p50 (median)", BLUE, SOLID, 4),
        ("B", 'histogram_quantile(0.95, sum by (le) (rate(captioner_inference_duration_seconds_bucket[1m])))', "p95", ORANGE, DASH, 6),
        ("C", 'histogram_quantile(0.99, sum by (le) (rate(captioner_inference_duration_seconds_bucket[1m])))', "p99", VERM, DOT, 8)],
    12, y, 12, 8, "s", "Latency, s", scale="log",
    unit_scale="seconds; LOG10 Y axis (bucket-interpolated estimate, accurate to bucket width)."))
y += 8
panels.append(timeseries(
    "Mean latency", "s per image (Summary)",
    "Mean = total inference seconds / images, per mode. Compare with the percentiles: mean >> p50 means a long tail.",
    [
        ("A", 'sum(rate(captioner_client_inference_seconds_sum{mode="single"}[1m])) / sum(rate(captioner_client_inference_seconds_count{mode="single"}[1m]))', "Single", BLUE, SOLID, 4),
        ("B", 'sum(rate(captioner_client_inference_seconds_sum{mode="bulk"}[1m])) / sum(rate(captioner_client_inference_seconds_count{mode="bulk"}[1m]))', "Bulk", ORANGE, DASH, 7)],
    0, y, 8, 8, "s", "Mean s/image", ymin=0))
panels.append(timeseries(
    "Errors", "failures per second by cause",
    "Flat at zero is healthy. Spikes name the cause: corrupt image, bad/empty ZIP, or a model failure.",
    [
        ("A", 'sum(rate(captioner_errors_total{error_type="invalid_image"}[1m]))', "invalid image", ORANGE, DASH, 6),
        ("B", 'sum(rate(captioner_errors_total{error_type="inference_error"}[1m]))', "inference error", VERM, SOLID, 8),
        ("C", 'sum(rate(captioner_errors_total{error_type=~"zip_.*|unreadable_entry"}[1m]))', "ZIP problems", SKY, DOT, 5)],
    8, y, 8, 8, "suffix: err/s", "Errors/s", ymin=0))
panels.append(timeseries(
    "Queue", "active vs waiting for model",
    "Waiting > 0 means the one-at-a-time CPU model is saturated: users queue and latency rises.",
    [("A", 'sum(captioner_active_requests)', "Active requests", BLUE, SOLID, 4),
     ("B", 'sum(captioner_waiting_for_model)', "Images waiting for model", VERM, DASH, 6)],
    16, y, 8, 8, "short", "Count", ymin=0))
y += 8

# Row 3 : CLIENTS
panels.append(row("3 · Clients — who is using the app (custom labels)", y)); y += 1
panels.append(text_panel(
    "Story", "Metrics carry **client_ip**, **session_id** and **browser** labels. The table joins volume and mean "
    "latency per client; the bar chart ranks IPs; the time-series splits request rate by browser family "
    "(a bounded label, so cardinality stays low). Open the app from a phone/second browser to add clients.",
    0, y, 24, 3))
y += 3
tbl = {"id": _id(), "type": "table", "title": "Clients · per-IP volume and latency",
       "description": desc("Per-client usage table",
                           "Who sent how many images and how fast they were served. Built from the labelled Counter "
                           "and Summary (_sum/_count).", "images = count (linear); latency = seconds (linear)."),
       "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 12, "h": 8},
       "targets": [
           _target("A", 'sum by (client_ip, mode) (captioner_client_images_total)', table=True),
           _target("B", 'sum by (client_ip, mode) (captioner_client_inference_seconds_sum) / sum by (client_ip, mode) (captioner_client_inference_seconds_count)', table=True)],
       "transformations": [
           {"id": "merge", "options": {}},
           {"id": "organize", "options": {
               "excludeByName": {"Time": True},
               "renameByName": {"client_ip": "Client IP", "mode": "Mode",
                                "Value #A": "Images captioned (count)", "Value #B": "Mean latency (s)"}}}],
       "fieldConfig": {"defaults": {"custom": {"align": "auto"}, "color": {"mode": "fixed", "fixedColor": BLUE}},
                       "overrides": [{"matcher": {"id": "byName", "options": "Mean latency (s)"},
                                      "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 2}]}]},
       "options": {"showHeader": True, "cellHeight": "sm"}}
panels.append(tbl)
panels.append(bargauge(
    "By IP", "total images",
    "Longer bar = heavier user. Different IPs appear when the app is opened from another device.",
    'sum by (client_ip) (captioner_client_images_total)', "{{client_ip}}", 12, y, 5, 8, unit="short"))
panels.append(timeseries(
    "By browser", "requests per second",
    "Splits traffic by User-Agent family: useful to spot a single scripted client (curl/python) generating load.",
    [
        ("A", 'sum(rate(captioner_client_requests_total{browser="chrome"}[15s]))', "Chrome", BLUE, SOLID, 4),
        ("B", 'sum(rate(captioner_client_requests_total{browser="firefox"}[15s]))', "Firefox", ORANGE, DASH, 6),
        ("C", 'sum(rate(captioner_client_requests_total{browser=~"safari|edge"}[15s]))', "Safari / Edge", GREEN, DOT, 5),
        ("D", 'sum(rate(captioner_client_requests_total{browser=~"curl|python|other"}[15s]))', "curl / python / other", PURPLE, DASHDOT, 7)],
    17, y, 7, 8, "suffix: req/s", "Requests/s", ymin=0))
y += 8

# Row 4 : SYSTEM (USE)
panels.append(row("4 · System resources — node_exporter (Utilisation · Saturation · Errors)", y)); y += 1
panels.append(text_panel(
    "Story", "Hardware view from **node_exporter**. CPU is split into user/system/iowait; RAM and disk are shown with the "
    "app's own memory on a second axis; load per core above 1 means runnable work is queueing for a core.", 0, y, 24, 3))
y += 3
panels.append(timeseries(
    "CPU", "% busy, split by mode",
    "A bulk ZIP drives user-mode CPU up for the whole batch; iowait stays low because the job is compute-bound.",
    [
        ("A", '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[15s])))', "Total busy", BLACK, SOLID, 4),
        ("B", '100 * avg(rate(node_cpu_seconds_total{mode="user"}[15s]))', "user", BLUE, DASH, 5),
        ("C", '100 * avg(rate(node_cpu_seconds_total{mode="system"}[15s]))', "system", ORANGE, DOT, 5),
        ("D", '100 * avg(rate(node_cpu_seconds_total{mode="iowait"}[15s]))', "iowait", VERM, DASHDOT, 6)],
    0, y, 8, 8, "percent", "CPU %", ymin=0, ymax=100))
panels.append(timeseries(
    "Memory", "RAM % (L), app bytes (R)",
    "RAM used % = 1 - available/total. App RSS (right axis) steps up when the model loads and on large ZIPs.",
    [
        ("A", '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)', "Machine RAM used (%)", BLUE, SOLID, 4),
        ("B", 'process_resident_memory_bytes{job="python_app"}', "App resident memory (bytes)", ORANGE, DASH, 6)],
    8, y, 8, 8, "percent", "RAM used %", ymin=0, ymax=100,
    right={"refs": ["B"], "unit": "bytes", "label": "App RSS, bytes", "min": 0}))
panels.append(timeseries(
    "Disk", "used % (L), I/O bytes/s (R)",
    "Used % is capacity pressure; read/write bytes/s show model-cache reads at startup and any swap or log writes.",
    [
        ("A", '100 * (1 - max(node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"}) / max(node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"}))', "Root disk used (%)", BLUE, SOLID, 4),
        ("B", 'sum(rate(node_disk_read_bytes_total[15s]))', "Disk read (B/s)", ORANGE, DASH, 6),
        ("C", 'sum(rate(node_disk_written_bytes_total[15s]))', "Disk write (B/s)", VERM, DOT, 6)],
    16, y, 8, 8, "percent", "Disk used %", ymin=0, ymax=100,
    right={"refs": ["B", "C"], "unit": "Bps", "label": "I/O, bytes/s", "min": 0}))
y += 8
panels.append(timeseries(
    "Saturation", "1-min load per core",
    "Above 1.0 means more runnable threads than cores: work is waiting. Normalised by core count so it is comparable across machines.",
    [
        ("A", 'node_load1 / scalar(count(count by (cpu) (node_cpu_seconds_total)))', "load1 per core", BLUE, SOLID, 4)],
    0, y, 12, 7, "none", "Load/core", ymin=0))
panels.append(timeseries(
    "Network", "bytes/s in and out",
    "Shows uploads from other devices (LAN). Uploads from this same machine use loopback and are not counted here.",
    [
        ("A", 'sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*|br-.*"}[15s]))', "Receive (B/s)", BLUE, SOLID, 4),
        ("B", 'sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|docker.*|br-.*"}[15s]))', "Transmit (B/s)", ORANGE, DASH, 6)],
    12, y, 12, 7, "Bps", "Network, B/s", ymin=0))
y += 7

# Row 5 : CORRELATION
panels.append(row("5 · Correlation — application load vs hardware", y)); y += 1
panels.append(text_panel(
    "Story", "**The point of the assignment:** each panel overlays an application metric (left axis) on a hardware metric "
    "(right axis) with a shared crosshair. Start a bulk ZIP and watch throughput/active requests rise **together with** CPU; "
    "when the batch ends both fall back.", 0, y, 24, 3))
y += 3
panels.append(timeseries(
    "Throughput vs CPU", "img/s (L), CPU % (R)",
    "Throughput rising and CPU rising together proves the workload is CPU-bound.",
    [("A", 'sum(rate(captioner_images_processed_total{status="success"}[15s]))', "Throughput (img/s)", BLUE, SOLID, 4),

     ("B", '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[15s])))', "Machine CPU busy (%)", VERM, DASH, 6)],
    0, y, 12, 9, "suffix: img/s", "Images/s", ymin=0,
    right={"refs": ["B"], "unit": "percent", "label": "CPU busy %", "min": 0, "max": 100}))
panels.append(timeseries(
    "Active requests vs app CPU", "count (L), % of one core (R)",
    "App CPU (100 % = one core fully used) tracks active requests; values over 100 % mean multi-threaded inference.",
    [("A", 'sum(captioner_active_requests)', "Active requests", BLUE, SOLID, 4),

     ("B", '100 * rate(process_cpu_seconds_total{job="python_app"}[15s])', "App CPU (% of one core)", ORANGE, DASH, 6)],
    12, y, 12, 9, "short", "Active requests", ymin=0,
    right={"refs": ["B"], "unit": "percent", "label": "App CPU, % of 1 core", "min": 0}))
y += 9
panels.append(timeseries(
    "p95 latency vs CPU", "seconds (L), CPU % (R)",
    "When CPU saturates, p95 latency climbs: the dashboard's saturation-to-user-impact link.",
    [("A", 'histogram_quantile(0.95, sum by (le) (rate(captioner_inference_duration_seconds_bucket[1m])))', "p95 latency (s)", BLUE, SOLID, 4),
     ("B", '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[15s])))', "Machine CPU busy (%)", VERM, DASH, 6)],
    0, y, 12, 9, "s", "p95 latency, s", ymin=0,
    right={"refs": ["B"], "unit": "percent", "label": "CPU busy %", "min": 0, "max": 100}))
panels.append(timeseries(
    "Bulk progress vs memory", "% done (L), app bytes (R)",
    "Progress climbs 0 → 100 % per ZIP; memory should plateau (no leak). A steady upward drift across batches would signal a leak.",
    [("A", '100 * captioner_batch_progress_ratio', "Batch progress (%)", BLUE, SOLID, 4),
     ("B", 'process_resident_memory_bytes{job="python_app"}', "App resident memory (bytes)", ORANGE, DASH, 6)],
    12, y, 12, 9, "percent", "Batch %", ymin=0, ymax=100,
    right={"refs": ["B"], "unit": "bytes", "label": "App RSS, bytes", "min": 0}))
y += 9

dashboard = {
    "uid": "da3408-a4-captioner",
    "title": "DA3408 A4 · AI Image Captioner Observability (vrishab)",
    "description": "CPU-only BLIP image captioner: health → application (RED) → clients → system (USE) → correlation. "
                   "PromQL drafted with AI assistance; prompts logged in build_dashboard.py.",
    "tags": ["da3408", "assignment-4", "captioner", "vrishab"],
    "timezone": "browser", "editable": True, "graphTooltip": 1,          # shared crosshair across panels
    "refresh": "5s", "time": {"from": "now-5m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m"]},
    "schemaVersion": 41, "version": 1, "fiscalYearStartMonth": 0, "liveNow": False,
    "links": [
        {"title": "Streamlit app", "type": "link", "url": "http://localhost:8501", "targetBlank": True, "icon": "external link"},
        {"title": "Prometheus targets", "type": "link", "url": "http://localhost:9090/targets", "targetBlank": True, "icon": "external link"},
        {"title": "Prometheus alerts", "type": "link", "url": "http://localhost:9090/alerts", "targetBlank": True, "icon": "bolt"},
        {"title": "App /metrics", "type": "link", "url": "http://localhost:18000/metrics", "targetBlank": True, "icon": "doc"}],
    "annotations": {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True,
         "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},

        {"name": "Alerts firing", "datasource": DS, "enable": True, "iconColor": VERM,
         "expr": 'ALERTS{alertstate="firing"}', "titleFormat": "{{alertname}}", "step": "5s"}]},
    "templating": {"list": [
        {"name": "ds", "label": "Prometheus", "type": "datasource", "query": "prometheus",
         "refresh": 1, "hide": 0, "current": {}, "options": [], "regex": ""}]},
    "panels": panels,
}

out = pathlib.Path(__file__).with_name("dashboard.json")
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out}  ({len(panels)} panels)")
