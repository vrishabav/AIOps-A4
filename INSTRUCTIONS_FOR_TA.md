# Instructions for TAs — DA3408 Assignment 4

Everything to grade is in this repository root (no nested project folder). `README.md` explains the project and gives the
setup and run commands; this file says where to look for each rubric item, what a correct run looks like, and where the
recorded evidence is.

## 1. Where to look, by rubric item

| Rubric item (marks) | Files | Live check (after starting the stack, see README "Run") |
|---|---|---|
| Python instrumentation (25) | `metrics.py` defines every metric (Counter, Gauge, Histogram, Summary, Info and the labels); `pipeline.py` and `app.py` update them | http://localhost:18000/metrics |
| Node exporting (5) | `prometheus.yml` (job `node` → `localhost:9100`, job `python_app` → `localhost:18000`) | http://localhost:9090/targets (four targets UP) |
| Grafana intuitiveness (20) | `dashboard.json` (the export); `build_dashboard.py` generates it and holds the PromQL prompt log | http://localhost:3000 (dashboard loads automatically, no login) |
| Application logic (10) | `app.py` (UI), `pipeline.py` (single and ZIP processing); `make_sample_zips.py` creates the test uploads | http://localhost:8501 |
| AI disclosure / PromQL attribution | README "AI disclosure"; prompt logs in the headers of `rules.yml` and `build_dashboard.py`; every query explained in README "Query logic" | — |

## 2. Quick eval run

Set up and start as in the README, then open http://localhost:8501 once (the metrics server and model start with the first
browser session). Expected results:

| Step | Action | What you should see |
|---|---|---|
| 1 | `./run.sh status` | Six services UP; four Prometheus targets `up` |
| 2 | Single tab: upload `sample_data/cat.jpg`, *Generate caption* | Caption "two cats sleeping on a couch" and the inference time (about 1 s once warm) |
| 3 | `curl -s localhost:18000/metrics \| grep 'captioner_images_processed_total'` | `mode="single",status="success"` is 1 |
| 4 | Bulk tab: upload `sample_data/bulk_60.zip` (or `bulk_20.zip` for a shorter run), *Process ZIP* | Progress bar, then "Done: 60 captioned, 0 failed" and a results table |
| 5 | Grafana during step 4 | Throughput, active requests and machine CPU rise together (row 5), then fall when the batch ends |
| 6 | Upload `mixed.zip`, `corrupt.zip`, `empty.zip` | Per-file error rows or an error box; the app keeps running |
| 7 | `kill $(cat .run/streamlit.pid)` | *App* tile turns DOWN; `CaptionerAppDown` goes FIRING at http://localhost:9090/alerts (the alert listener from `alertmanager.yml` prints it) |
| 8 | `./run.sh stop` (or `./run.sh reset` to also wipe stored data) | All processes stop |

Client labels: open the app from a second device or browser at `http://<machine-LAN-IP>:8501`; a new `client_ip` and
`session_id` appear in `captioner_client_images_total` and in the dashboard's clients row.

## 3. Recorded evidence (`outputs/`)

Captured on the author's machine (Linux, Python 3.14, 12 cores / 24 threads) on 2026-10-23. Folders are in evaluation order and
files are numbered in the order they were taken.

| Folder | What it shows | Rubric item |
|---|---|---|
| `00_terminal_logs/` | `1_…`: setup, reset, start, status. `2_…`: the alert listener's log (CPU alert fired and resolved twice, app-down alert fired and resolved), then `stop` and `reset` | all |
| `01_app_single_and_bulk/` | `01–03` start-up and model load; `04` single caption; `05–07` mixed, corrupt and empty ZIPs handled; `08–10` two `bulk_60.zip` runs (progress, then 60 captioned); `11` the app opened from a second client (LAN IP) | Application logic, Instrumentation (client labels) |
| `02_prometheus/` | `01–02` all four targets UP; `03–05` the five alert rules loaded | Node exporting |
| `03_raw_metrics/app_18000/` | The application's `/metrics`: counters with `mode`/`status`/`error_type`, client labels, gauges, histogram buckets, Summary `_sum`/`_count` (`05`) | Instrumentation |
| `03_raw_metrics/node_exporter_9100/` | node_exporter output, including `node_cpu_seconds_total` per core | Node exporting |
| `04_grafana_dashboard/` | `01` health row; `02–04` clients row filling up during a run; `05–09` the whole dashboard top to bottom, with the correlation panels (`09` shows both bulk runs); `10` a panel's description (story, scale, units) | Grafana intuitiveness |
| `05_alerting_and_recovery/` | `01` CPU alert shown as 1 firing alert on the dashboard; `02–03` app stopped: DOWN tile and `CaptionerAppDown` FIRING; `04` alerts received by the listener; `05–07` restart and recovery | Alerting (see note 2) |

## 4. Notes

1. **Summary suffixes.** The Python `prometheus_client` Summary exposes `_sum` and `_count`. The `_total` series come from the
   Counters (for example `captioner_images_processed_total`).
2. **Alerting.** The assignment text mentions an alerting pipeline in its objective but has no alerting section or deliverable.
   A small one is included (`rules.yml`, `alertmanager.yml`); it is not needed for the other rubric items.
3. **Dashboard JSON.** `dashboard.json` is generated by `build_dashboard.py` so that the seven plotting commandments are applied
   identically to every panel. It imports into Grafana like any other export (*Dashboards → New → Import*).
4. **Start-up order.** Streamlit runs `app.py` only when the first browser session connects, so the `python_app` target shows
   DOWN until http://localhost:8501 has been opened once.
5. **Timings** depend on the machine. The author measured about 1 s per image after warm-up with 12 torch threads.
