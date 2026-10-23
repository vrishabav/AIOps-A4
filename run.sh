#!/usr/bin/env bash
# One script to run the whole stack.   Usage:  ./run.sh setup | start | stop | status | reset
# Activate your Python venv first (it must contain streamlit, torch, transformers, prometheus_client).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$ROOT/.run"                                   # pids, logs, Prometheus + Grafana data (git-ignored)
TOOLS="${TOOLS_DIR:-$ROOT/tools}"                  # where Prometheus / node_exporter / Alertmanager / Grafana live
CLASS_DIR="${CLASS_DIR:-$ROOT/../context/Monitoring}"   # optional: reuse the binaries from the class folder
export HF_HOME="${HF_HOME:-$ROOT/.hf_cache}"       # model cache next to the project (set HF_HOME to change)
GRAFANA_VERSION=12.2.0

# find_tool <glob>  -> first match in $TOOLS then $CLASS_DIR
find_tool() { local d f; for d in "$TOOLS" "$CLASS_DIR"; do for f in "$d"/$1; do [ -d "$f" ] && { echo "$f"; return; }; done; done; }

fetch() {  # fetch <name> <glob> <url>   (download + extract into $TOOLS if not already present)
  [ -n "$(find_tool "$2")" ] && return
  echo "Downloading $1 ..."; mkdir -p "$TOOLS"
  curl -fL "$3" | tar xz -C "$TOOLS" || { echo "download of $1 failed"; exit 1; }
}

start_bg() {  # start_bg <name> <command...>
  local name=$1; shift
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then echo "$name already running"; return; fi
  nohup "$@" > "$RUN/$name.log" 2>&1 &
  echo $! > "$RUN/$name.pid"; echo "started $name (pid $!)"
}

case "${1:-}" in
setup)
  python -c "import sys; print('python', sys.version.split()[0])"
  pip install torch --index-url https://download.pytorch.org/whl/cpu      # CPU-only wheel (no CUDA download)
  pip install -r "$ROOT/requirements.txt"
  fetch prometheus   'prometheus-*.linux-amd64'    https://github.com/prometheus/prometheus/releases/download/v3.15.0/prometheus-3.15.0.linux-amd64.tar.gz
  fetch node_exporter 'node_exporter-*.linux-amd64' https://github.com/prometheus/node_exporter/releases/download/v1.12.1/node_exporter-1.12.1.linux-amd64.tar.gz
  fetch alertmanager 'alertmanager-*.linux-amd64'  https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-0.34.1.linux-amd64.tar.gz
  fetch grafana      'grafana-*'                   https://dl.grafana.com/oss/release/grafana-$GRAFANA_VERSION.linux-amd64.tar.gz
  echo "Pre-downloading the BLIP model into $HF_HOME (~1 GB, once) ..."
  python -c "from transformers import BlipProcessor, BlipForConditionalGeneration as B; i='Salesforce/blip-image-captioning-base'; BlipProcessor.from_pretrained(i); B.from_pretrained(i); print('model ready')"
  ;;

start)
  python -c "import streamlit, torch, transformers, prometheus_client" 2>/dev/null || { echo "Activate the venv and run ./run.sh setup first"; exit 1; }
  PROM=$(find_tool 'prometheus-*.linux-amd64'); NODE=$(find_tool 'node_exporter-*.linux-amd64')
  AM=$(find_tool 'alertmanager-*.linux-amd64'); GRAF=$(find_tool 'grafana-*')
  for t in "$PROM" "$NODE" "$AM" "$GRAF"; do [ -n "$t" ] || { echo "missing tool - run ./run.sh setup"; exit 1; }; done
  mkdir -p "$RUN/provisioning/datasources" "$RUN/provisioning/dashboards" "$RUN/dashboards"
  cp "$ROOT/dashboard.json" "$RUN/dashboards/"       # Grafana loads dashboard.json from here

  # Grafana provisioning, generated here so the repo needs no extra folders:
  cat > "$RUN/provisioning/datasources/prometheus.yml" <<EOF
apiVersion: 1
datasources:
  - {name: Prometheus, type: prometheus, uid: prometheus, access: proxy, url: "http://localhost:9090", isDefault: true, jsonData: {timeInterval: 1s}}
EOF
  cat > "$RUN/provisioning/dashboards/dashboards.yml" <<EOF
apiVersion: 1
providers:
  - {name: da3408, type: file, allowUiUpdates: true, updateIntervalSeconds: 10, options: {path: "$RUN/dashboards"}}
EOF

  start_bg node_exporter "$NODE/node_exporter"                                            # :9100
  start_bg alertmanager  "$AM/alertmanager" --config.file="$ROOT/alertmanager.yml" --storage.path="$RUN/alertmanager"   # :9093
  start_bg prometheus    "$PROM/prometheus" --config.file="$ROOT/prometheus.yml" --storage.tsdb.path="$RUN/prometheus" --web.enable-lifecycle   # :9090
  GF_PATHS_DATA="$RUN/grafana" GF_PATHS_LOGS="$RUN/grafana-logs" GF_PATHS_PLUGINS="$RUN/grafana-plugins" \
  GF_PATHS_PROVISIONING="$RUN/provisioning" GF_SERVER_HTTP_PORT=3000 \
  GF_AUTH_ANONYMOUS_ENABLED=true GF_AUTH_ANONYMOUS_ORG_ROLE=Admin GF_AUTH_DISABLE_LOGIN_FORM=true \
  GF_DASHBOARDS_MIN_REFRESH_INTERVAL=1s GF_ANALYTICS_REPORTING_ENABLED=false GF_ANALYTICS_CHECK_FOR_UPDATES=false \
  start_bg grafana "$GRAF/bin/grafana" server --homepath "$GRAF"                           # :3000
  (cd "$ROOT" && start_bg streamlit python -m streamlit run app.py --server.port 8501 --server.headless true \
      --browser.gatherUsageStats false --server.fileWatcherType none)                      # :8501 (+ metrics :18000)
  echo
  echo "Open http://localhost:8501 ONCE - the metrics server (:18000) and the model start with the first browser session."
  echo "Grafana http://localhost:3000   Prometheus http://localhost:9090   Alertmanager http://localhost:9093"
  ;;

stop)
  for f in "$RUN"/*.pid; do
    [ -e "$f" ] || continue
    n=$(basename "$f" .pid); pid=$(cat "$f"); kill "$pid" 2>/dev/null || { rm -f "$f"; continue; }
    for _ in $(seq 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done      # wait up to 15 s for a clean exit
    kill -0 "$pid" 2>/dev/null && { kill -9 "$pid"; echo "killed $n (did not exit)"; } || echo "stopped $n"
    rm -f "$f"
  done
  ;;

status)
  for p in "node_exporter 9100 /metrics" "alertmanager 9093 /-/healthy" "prometheus 9090 /-/healthy" \
           "grafana 3000 /api/health" "streamlit 8501 /_stcore/health" "app-metrics 18000 /metrics"; do
    set -- $p
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://localhost:$2$3"); [ "$code" = 200 ] && s=UP || s="DOWN($code)"
    printf '%-14s :%-6s %s\n' "$1" "$2" "$s"
  done
  echo; echo "Prometheus targets:"
  curl -s -m 3 localhost:9090/api/v1/targets | python -c "import sys,json; [print(' ',t['labels']['job'],t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]" 2>/dev/null || echo "  (Prometheus not reachable)"
  ;;

reset)   # stop everything and wipe all stored data: metrics history, Grafana state, logs
  "$0" stop; for _ in 1 2 3 4 5; do rm -rf "$RUN" 2>/dev/null; [ -e "$RUN" ] || break; sleep 1; done; echo "wiped $RUN  (model cache $HF_HOME kept)"
  ;;

*) echo "usage: ./run.sh setup | start | stop | status | reset"; exit 1 ;;
esac
