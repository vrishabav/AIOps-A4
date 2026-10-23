"""DA3408 A4 - CPU image captioning (Streamlit) with Prometheus instrumentation.

Run:  streamlit run app.py        (UI on :8501, Prometheus /metrics on :18000)
"""
import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import metrics as m
import pipeline

st.set_page_config(page_title="CPU Image Captioning", page_icon="🖼️", layout="wide")


@st.cache_resource
def boot():
    """Runs once per Streamlit process (Streamlit re-runs this script on every click): metrics server + model."""
    m.start_metrics_server()
    return pipeline.load_model()


def client_info() -> pipeline.Client:
    """Client metadata used as Prometheus labels. Session id is shortened to keep cardinality low."""
    ctx = get_script_run_ctx()
    session = (ctx.session_id if ctx else "unknown")[:8]
    ip, ua = None, ""
    try:
        ip = st.context.ip_address
        ua = st.context.headers.get("User-Agent", "")
        ip = ip or st.context.headers.get("X-Forwarded-For", "").split(",")[0].strip() or None
    except Exception:
        pass
    ip = (ip or "127.0.0.1").removeprefix("::ffff:")            # IPv4-mapped IPv6 -> plain IPv4
    return pipeline.Client(ip, session, m.browser_family(ua))


bundle = boot()
client = client_info()
m.touch_session(client.session_id)

st.title("🖼️ CPU Image Captioning")
st.caption(f"Model: `{pipeline.MODEL_ID}` · CPU · {pipeline.NUM_THREADS} threads · "
           f"you: {client.ip} / session {client.session_id} / {client.browser} · "
           f"metrics: http://localhost:{m.METRICS_PORT}/metrics")

tab_single, tab_bulk = st.tabs(["Single image", "Bulk (ZIP)"])

with tab_single:
    up = st.file_uploader("Upload an image", type=[e.strip(".") for e in pipeline.IMAGE_EXTS], key="single_up")
    if up is not None:
        st.image(up, width=360)
        if st.button("Generate caption", type="primary", key="single_go"):
            with st.spinner("Captioning on CPU..."):
                res = pipeline.process_single(bundle, up.getvalue(), up.name, client)
            if res.status == "success":
                st.success(res.caption)
                st.caption(f"Inference time: {res.seconds:.2f} s")
            else:
                st.error(res.error)

with tab_bulk:
    st.caption(f"ZIP ≤ {pipeline.MAX_ZIP_MB} MB, first {pipeline.MAX_IMAGES} images are captioned.")
    zp = st.file_uploader("Upload a ZIP of images", type=["zip"], key="bulk_up")
    if zp is not None and st.button("Process ZIP", type="primary", key="bulk_go"):
        bar = st.progress(0.0, text="Starting...")

        def on_progress(i, total, res):
            bar.progress(i / total, text=f"{i}/{total}  {res.filename}  ({res.status})")

        results = pipeline.process_zip(bundle, zp.getvalue(), client, on_progress)
        bar.empty()
        df = pd.DataFrame([r.__dict__ for r in results])
        ok = int((df["status"] == "success").sum())
        if ok == 0:
            st.error(f"Nothing captioned: {df['error'].iloc[0]}")
        else:
            st.success(f"Done: {ok} captioned, {len(df) - ok} failed")
        st.dataframe(df, width="stretch")
        st.download_button("Download CSV", df.to_csv(index=False), "captions.csv", "text/csv")
