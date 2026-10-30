"""
AI-Based Industrial Equipment Health Monitor  (v2 - redesigned UI)
-------------------------------------------------------------------
Run:   streamlit run equipment_dashboard.py
Needs: pip install streamlit streamlit-autorefresh pandas numpy plotly paho-mqtt
       (Streamlit >= 1.50 recommended: uses pills, segmented controls, chart click-selection)

* A background simulator generates realistic multi-sensor data (load cycles,
  slow wear, random faults) so the graphs move up and down live.
* Health score = weighted penalty model + sudden-change (z-score) anomaly
  detector, smoothed with an EWMA. Remaining useful life (RUL) comes from the
  health trend.
* Set USE_MQTT=1 to ingest real data instead/as well (JSON payload with
  equipment_id, timestamp and every sensor key listed in SENSORS).
"""
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Allow the regular VS Code "Run Python File" button to launch this app too.
if not st.runtime.exists():
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                os.path.abspath(__file__),
                "--server.headless",
                "false",
                "--server.port",
                "8502",
            ]
        )
    )

from streamlit_autorefresh import st_autorefresh

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
DB_PATH = "equipment_health.db"
TABLE = "readings"
HISTORY_BACKFILL = 150        # readings generated on first start so charts aren't empty
MAX_ROWS_PER_EQ = 600         # rows loaded per machine for the dashboard
FAULT_PROB = 0.003            # chance per reading that a machine starts a fault
WEAR_RATE = 0.0003            # slow degradation per reading
TS_FMT = "%Y-%m-%d %H:%M:%S"

USE_MQTT = os.getenv("USE_MQTT", "0") == "1"
BROKER_HOST = os.getenv("BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "test/topic")

# mode: "high" = bad when above nominal, "low" = bad when below, "band" = bad both ways
# span: deviation from nominal that means full penalty ("trip")
# warn: deviation from nominal that raises a warning alert
# weight: share of the health score this sensor controls
# jitter/load/wear: simulator noise, load sensitivity, wear sensitivity
SENSORS = {
    "temperature":  dict(label="Motor Temp",    unit="°C",   icon="🌡", nominal=65,   span=30,   warn=15,   mode="high", weight=0.15, jitter=0.8,   load=1.5,  wear=0.25),
    "bearing_temp": dict(label="Bearing Temp",  unit="°C",   icon="🔥", nominal=55,   span=35,   warn=22,   mode="high", weight=0.14, jitter=0.6,   load=1.0,  wear=0.35),
    "vibration":    dict(label="Vibration",     unit="mm/s", icon="📳", nominal=2.0,  span=5.0,  warn=2.5,  mode="high", weight=0.20, jitter=0.12,  load=0.3,  wear=0.30),
    "current":      dict(label="Current",       unit="A",    icon="⚡", nominal=15,   span=20,   warn=12,   mode="high", weight=0.12, jitter=0.3,   load=1.5,  wear=0.15),
    "voltage":      dict(label="Voltage",       unit="V",    icon="🔌", nominal=415,  span=50,   warn=30,   mode="band", weight=0.06, jitter=1.5,   load=-0.2, wear=0.0),
    "power_factor": dict(label="Power Factor",  unit="",     icon="📐", nominal=0.90, span=0.20, warn=0.08, mode="low",  weight=0.05, jitter=0.005, load=-0.3, wear=-0.30),
    "rpm":          dict(label="RPM",           unit="rpm",  icon="🔄", nominal=1450, span=200,  warn=100,  mode="band", weight=0.08, jitter=4.0,   load=-0.5, wear=-0.10),
    "pressure":     dict(label="Pressure",      unit="bar",  icon="🧭", nominal=3.5,  span=2.0,  warn=1.0,  mode="band", weight=0.08, jitter=0.05,  load=0.2,  wear=-0.15),
    "flow":         dict(label="Flow",          unit="m³/h", icon="💧", nominal=120,  span=60,   warn=30,   mode="band", weight=0.06, jitter=1.5,   load=0.2,  wear=-0.20),
    "noise_db":     dict(label="Acoustic Noise", unit="dB",  icon="🔊", nominal=70,   span=25,   warn=12,   mode="high", weight=0.09, jitter=0.8,   load=0.5,  wear=0.30),
}

# Per-machine nominal values (anything missing falls back to SENSORS[...]["nominal"])
PROFILES = {
    "MOTOR-01":      {"temperature": 65, "current": 15, "rpm": 1450},
    "PUMP-01":       {"pressure": 4.2, "flow": 140, "rpm": 1480, "current": 18},
    "COMPRESSOR-01": {"pressure": 7.0, "flow": 90, "rpm": 1620, "current": 22, "temperature": 72},
    "FAN-01":        {"rpm": 1200, "flow": 200, "pressure": 1.5, "current": 9, "temperature": 55},
    "GEARBOX-01":    {"rpm": 900, "current": 12, "bearing_temp": 60, "vibration": 2.4, "pressure": 2.5, "flow": 60},
}

# Fault signatures: additive effect at peak severity
FAULTS = {
    "bearing_wear": {"vibration": 4.0, "bearing_temp": 22, "noise_db": 14, "temperature": 6},
    "overload":     {"current": 14, "temperature": 20, "rpm": -90, "power_factor": -0.08},
    "cavitation":   {"pressure": -1.4, "flow": -45, "vibration": 2.5, "noise_db": 12},
    "overheating":  {"temperature": 28, "bearing_temp": 15, "current": 4},
    "voltage_sag":  {"voltage": -45, "current": 6, "rpm": -50},
}

OVERVIEW = "Overview"
STATUS_RANK = {"NORMAL": 0, "GOOD": 1, "WARNING": 2, "CRITICAL": 3, "SEVERE": 4}
STATUS_GROUP = {"NORMAL": "Healthy", "GOOD": "Healthy", "WARNING": "Warning",
                "CRITICAL": "Critical", "SEVERE": "Critical"}
STATUS_COLOR = {"NORMAL": "#34d399", "GOOD": "#86efac", "WARNING": "#fbbf24",
                "CRITICAL": "#fb923c", "SEVERE": "#f87171"}
SENSOR_COLOR = {"ok": "#34d399", "warn": "#fbbf24", "trip": "#f87171"}
PALETTE = ["#38bdf8", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#fb923c",
           "#22d3ee", "#c084fc", "#4ade80", "#f87171"]
GRID = "rgba(143,163,187,0.14)"
ACCENT = "#38bdf8"


def nominal_of(eq, key):
    return PROFILES.get(eq, {}).get(key, SENSORS[key]["nominal"])


def status_from_health(score):
    if score >= 90:
        return "NORMAL"
    if score >= 70:
        return "GOOD"
    if score >= 50:
        return "WARNING"
    if score >= 30:
        return "CRITICAL"
    return "SEVERE"


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
def _connect():
    return sqlite3.connect(DB_PATH, timeout=10)


def init_db():
    cols = ", ".join(f"{k} REAL NOT NULL" for k in SENSORS)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} "
            f"(equipment_id TEXT NOT NULL, timestamp TEXT NOT NULL, {cols})"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_eq_ts ON {TABLE}(equipment_id, timestamp)")
        conn.commit()
    finally:
        conn.close()


def insert_rows(rows):
    names = ["equipment_id", "timestamp"] + list(SENSORS)
    sql = f"INSERT INTO {TABLE} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
    conn = _connect()
    try:
        conn.executemany(sql, [[r[n] for n in names] for r in rows])
        conn.commit()
    finally:
        conn.close()


def prune_db(keep=20000):
    conn = _connect()
    try:
        conn.execute(
            f"DELETE FROM {TABLE} WHERE rowid NOT IN "
            f"(SELECT rowid FROM {TABLE} ORDER BY rowid DESC LIMIT ?)", (keep,))
        conn.commit()
    finally:
        conn.close()


def count_rows():
    conn = _connect()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    finally:
        conn.close()


def load_data(limit=MAX_ROWS_PER_EQ):
    conn = _connect()
    try:
        df = pd.read_sql(
            f"""SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY equipment_id
                                                 ORDER BY timestamp DESC) AS rn
                    FROM {TABLE}) WHERE rn <= ? ORDER BY equipment_id, timestamp""",
            conn, params=(limit,))
    finally:
        conn.close()
    df["time"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["time"]).drop(columns=["rn"])
    return df.drop_duplicates(["equipment_id", "time"], keep="last").reset_index(drop=True)


# ----------------------------------------------------------------------------
# Simulator (background thread -> writes into the same DB as MQTT would)
# ----------------------------------------------------------------------------
def generate_reading(eq, state):
    t = state["t"]
    state["t"] += 1
    load = 1 + 0.08 * math.sin(t / 25 + state["phase"]) + 0.03 * math.sin(t / 6.5)

    sev, effects, fault = 0.0, {}, state["fault"]
    if fault:
        progress = fault["elapsed"] / fault["duration"]
        sev = math.sin(math.pi * min(progress, 1.0))     # ramps up, peaks, recovers
        effects = FAULTS[fault["type"]]
        fault["elapsed"] += 1
        if fault["elapsed"] >= fault["duration"]:
            state["fault"] = None

    row = {"equipment_id": eq}
    for key, s in SENSORS.items():
        value = (nominal_of(eq, key)
                 + (load - 1) * s["load"] * s["span"]
                 + state["wear"] * s["wear"] * s["span"]
                 + sev * effects.get(key, 0.0)
                 + random.gauss(0, s["jitter"]))
        value = max(value, 0.0)
        if key == "power_factor":
            value = min(value, 1.0)
        row[key] = round(value, 3)
    state["wear"] = min(1.0, state["wear"] + WEAR_RATE)
    return row


class Simulator:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = True
        self.interval = 2.0
        self.ticks = 0
        self.state = {
            eq: {"wear": random.uniform(0.0, 0.15), "phase": random.uniform(0, 6.28),
                 "t": 0, "fault": None}
            for eq in PROFILES
        }
        if count_rows() < HISTORY_BACKFILL:                  # pre-fill so charts start populated
            now = datetime.now()
            for i in range(HISTORY_BACKFILL):
                self.step(now - timedelta(seconds=(HISTORY_BACKFILL - i) * self.interval))
        threading.Thread(target=self._loop, daemon=True).start()

    def step(self, ts):
        rows = []
        with self.lock:
            self.ticks += 1
            for eq, s in self.state.items():
                if s["fault"] is None and random.random() < FAULT_PROB:
                    s["fault"] = {"type": random.choice(list(FAULTS)), "elapsed": 0,
                                  "duration": random.randint(40, 90)}
                row = generate_reading(eq, s)
                row["timestamp"] = ts.strftime(TS_FMT)
                rows.append(row)
        insert_rows(rows)
        if self.ticks % 300 == 0:
            prune_db()

    def _loop(self):
        while True:
            time.sleep(self.interval)
            if self.running:
                try:
                    self.step(datetime.now())
                except Exception as exc:      # never let the thread die
                    print(f"[!] simulator error: {exc}")

    def inject(self, eq, fault_type, duration=60):
        with self.lock:
            self.state[eq]["fault"] = {"type": fault_type, "elapsed": 0, "duration": duration}

    def maintain(self, eq):
        with self.lock:
            self.state[eq]["wear"] = 0.0
            self.state[eq]["fault"] = None


@st.cache_resource
def get_simulator():
    init_db()
    return Simulator()


# ----------------------------------------------------------------------------
# Optional MQTT ingestion (real sensors)
# ----------------------------------------------------------------------------
@st.cache_resource
def start_mqtt():
    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(MQTT_TOPIC)
            print(f"[+] MQTT connected, subscribed to '{MQTT_TOPIC}'")

    def on_message(client, userdata, message):
        try:
            p = json.loads(message.payload.decode("utf-8"))
            if {"equipment_id", "timestamp", *SENSORS}.issubset(p):
                insert_rows([{"equipment_id": p["equipment_id"], "timestamp": p["timestamp"],
                              **{k: float(p[k]) for k in SENSORS}}])
        except Exception as exc:
            print(f"[!] bad MQTT payload: {exc}")

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect, client.on_message = on_connect, on_message
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    return client


# ----------------------------------------------------------------------------
# AI health scoring
# ----------------------------------------------------------------------------
def score_health(df):
    """Adds pen_<sensor> (health points lost), anomaly, health_raw, health."""
    df = df.copy()
    total_penalty = pd.Series(0.0, index=df.index)

    # 1) Weighted deviation-from-nominal penalty model
    for key, s in SENSORS.items():
        nominal = df["equipment_id"].map(lambda e: nominal_of(e, key)).astype(float)
        dev = df[key] - nominal
        if s["mode"] == "high":
            dev = dev.clip(lower=0)
        elif s["mode"] == "low":
            dev = (-dev).clip(lower=0)
        else:
            dev = dev.abs()
        p = (dev / s["span"]).clip(0, 1) ** 1.5          # gentle near nominal, steep near trip
        df[f"pen_{key}"] = 100 * s["weight"] * p
        total_penalty += s["weight"] * p

    # 2) Sudden-change detector: rolling z-score against the machine's own recent past
    flags = pd.DataFrame(index=df.index)
    for key in SENSORS:
        g = df.groupby("equipment_id")[key]
        mean = g.transform(lambda s: s.shift(1).rolling(60, min_periods=20).mean())
        std = g.transform(lambda s: s.shift(1).rolling(60, min_periods=20).std())
        z = (df[key] - mean) / std.replace(0, np.nan)
        flags[key] = (z.abs() > 3).astype(float)
    df["anomaly"] = flags.mean(axis=1)                    # share of sensors behaving oddly

    # 3) Combine + smooth
    df["health_raw"] = (100 * (1 - total_penalty.clip(0, 1)) - 15 * df["anomaly"]).clip(0, 100)
    df["health"] = df.groupby("equipment_id")["health_raw"].transform(
        lambda s: s.ewm(span=4, adjust=False).mean())
    return df


def estimate_rul(d, floor=50, window=60):
    """Seconds until health trend reaches `floor`; None if not degrading."""
    tail = d.tail(window)
    if len(tail) < 15:
        return None
    x = (tail["time"] - tail["time"].iloc[0]).dt.total_seconds().to_numpy()
    slope, _ = np.polyfit(x, tail["health"].to_numpy(), 1)
    current = tail["health"].iloc[-1]
    if current <= floor:
        return 0.0
    if slope > -1e-3:
        return None
    return (current - floor) / -slope


def fmt_duration(seconds):
    if seconds is None:
        return "Stable"
    if seconds <= 0:
        return "Now"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def top_contributor(row):
    pens = {k: row[f"pen_{k}"] for k in SENSORS}
    key = max(pens, key=pens.get)
    return key, pens[key]


def limit_lines(eq, key):
    s, n = SENSORS[key], nominal_of(eq, key)
    if s["mode"] == "high":
        return [n + s["warn"]]
    if s["mode"] == "low":
        return [n - s["warn"]]
    return [n - s["warn"], n + s["warn"]]


def sensor_dev(eq, key, v):
    d = v - nominal_of(eq, key)
    mode = SENSORS[key]["mode"]
    if mode == "high":
        return max(d, 0.0)
    if mode == "low":
        return max(-d, 0.0)
    return abs(d)


def sensor_state(eq, key, v):
    d, s = sensor_dev(eq, key, v), SENSORS[key]
    return "trip" if d >= s["span"] else "warn" if d > s["warn"] else "ok"


def sensor_alerts(eq, row):
    msgs = []
    for key, s in SENSORS.items():
        v = row[key]
        lines = limit_lines(eq, key)
        if s["mode"] in ("high", "band") and v > max(lines):
            msgs.append((key, f"{s['label']} {v:.2f} {s['unit']} is above limit {max(lines):g}"))
        if s["mode"] in ("low", "band") and v < min(lines):
            msgs.append((key, f"{s['label']} {v:.2f} {s['unit']} is below limit {min(lines):g}"))
    return msgs


# ----------------------------------------------------------------------------
# Look & feel: CSS + small HTML helpers
# ----------------------------------------------------------------------------
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
:root{--bg:#0a111a;--panel:#111c2b;--panel2:#162439;--line:#1f3047;--text:#e6edf5;--muted:#8da2ba;--accent:#38bdf8;}
html,body,.stApp,.stApp :is(p,label,li,h1,h2,h3,h4,h5,h6,div,button,input,select,textarea,a,td,th){font-family:'IBM Plex Sans',system-ui,sans-serif;}
/* keep Streamlit's own icon font so arrows/chevrons render as icons, not text */
.stApp span[data-testid="stIconMaterial"],.stApp [data-testid="stIconMaterial"],.material-icons,.material-symbols-rounded{
 font-family:"Material Symbols Rounded","Material Icons"!important;}
.stApp{background:radial-gradient(1200px 500px at 85% -10%,rgba(56,189,248,.10),transparent 60%),var(--bg);color:var(--text);}
header[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:1.2rem;max-width:1500px;}
h1,h2,h3,h4{color:var(--text);letter-spacing:-.01em;font-weight:600;}
section[data-testid="stSidebar"]{background:#0d1623;border-right:1px solid var(--line);}
section[data-testid="stSidebar"] h2{font-size:1.05rem;}

/* hero */
.hero{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:18px 22px;margin-bottom:16px;
 border:1px solid var(--line);border-radius:14px;background:linear-gradient(110deg,#12233a 0%,#0f1b2b 60%);}
.hero-title{font-size:1.55rem;font-weight:700;letter-spacing:-.02em;}
.hero-sub{color:var(--muted);font-size:.92rem;margin-top:2px;}
.live{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:.85rem;white-space:nowrap;}
.dot{width:9px;height:9px;border-radius:50%;background:#34d399;box-shadow:0 0 0 0 rgba(52,211,153,.7);animation:pulse 2s infinite;}
.dot.off{background:#64748b;animation:none;}
@keyframes pulse{70%{box-shadow:0 0 0 9px rgba(52,211,153,0);}100%{box-shadow:0 0 0 0 rgba(52,211,153,0);}}
@media (prefers-reduced-motion:reduce){.dot{animation:none;}}

/* KPI tiles */
.kpi{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--c,var(--accent));border-radius:10px;padding:12px 16px;height:100%;}
.kpi-l{color:var(--muted);font-size:.8rem;}
.kpi-v{font-size:1.65rem;font-weight:700;line-height:1.25;}
.kpi-s{color:var(--muted);font-size:.78rem;}

/* pills */
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.75rem;font-weight:600;color:var(--c);
 background:color-mix(in srgb,var(--c) 14%,transparent);border:1px solid color-mix(in srgb,var(--c) 45%,transparent);}

/* equipment cards (streamlit bordered containers) */
div[data-testid="stVerticalBlockBorderWrapper"]{background:var(--panel);border-color:var(--line)!important;border-radius:14px;transition:border-color .15s,transform .15s;}
div[data-testid="stVerticalBlockBorderWrapper"]:hover{border-color:#2f4a6b!important;}
.eq-head{display:flex;align-items:center;gap:14px;}
.eq-name{font-size:1.1rem;font-weight:600;margin-bottom:4px;}
.eq-sub{color:var(--muted);font-size:.78rem;margin-top:4px;}
.eq-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0 4px;}
.stat{background:var(--panel2);border-radius:8px;padding:7px 10px;}
.stat-l{color:var(--muted);font-size:.72rem;}
.stat-v{font-weight:600;font-size:.98rem;}

/* sensor tiles */
.sgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;margin:6px 0 14px;}
.stile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;}
.stile-h{display:flex;justify-content:space-between;color:var(--muted);font-size:.8rem;}
.stile-v{font-size:1.3rem;font-weight:700;margin:2px 0 6px;}
.stile-u{font-size:.8rem;color:var(--muted);font-weight:500;margin-left:3px;}
.bar{height:5px;background:var(--panel2);border-radius:99px;overflow:hidden;}
.bar>span{display:block;height:100%;border-radius:99px;}

/* alert rows */
.alert{display:flex;gap:10px;align-items:flex-start;padding:10px 14px;margin-bottom:8px;background:var(--panel);
 border:1px solid var(--line);border-left:4px solid var(--c);border-radius:8px;font-size:.92rem;}
.alert b{color:var(--text);}
.alert-ok{border-left-color:#34d399;color:var(--muted);}

/* native widgets */
div[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;}
.stButton>button,.stDownloadButton>button{border-radius:8px;border:1px solid var(--line);background:var(--panel2);color:var(--text);transition:all .15s;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--accent);color:var(--accent);}
button[data-baseweb="tab"]{font-weight:600;}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:10px;overflow:hidden;}
details{border-color:var(--line)!important;border-radius:10px!important;}
</style>
"""


def pill(status):
    return f'<span class="pill" style="--c:{STATUS_COLOR[status]}">{status.title()}</span>'


def kpi(label, value, sub="", color=ACCENT):
    return (f'<div class="kpi" style="--c:{color}"><div class="kpi-l">{label}</div>'
            f'<div class="kpi-v">{value}</div><div class="kpi-s">{sub}</div></div>')


def ring_svg(score, color, size=84):
    r = 34
    c = 2 * math.pi * r
    off = c * (1 - max(0, min(score, 100)) / 100)
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 84 84">'
            f'<circle cx="42" cy="42" r="{r}" stroke="#1f3047" stroke-width="8" fill="none"/>'
            f'<circle cx="42" cy="42" r="{r}" stroke="{color}" stroke-width="8" fill="none" '
            f'stroke-linecap="round" stroke-dasharray="{c:.1f}" stroke-dashoffset="{off:.1f}" '
            f'transform="rotate(-90 42 42)"/>'
            f'<text x="42" y="48" text-anchor="middle" fill="#e6edf5" font-size="20" '
            f'font-weight="700">{score:.0f}</text></svg>')


def stat(label, value, color="inherit"):
    return (f'<div class="stat"><div class="stat-l">{label}</div>'
            f'<div class="stat-v" style="color:{color}">{value}</div></div>')


def themed(fig, height=None):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="IBM Plex Sans, sans-serif", color="#c3d1e3"),
                      colorway=PALETTE, hoverlabel=dict(bgcolor="#162439", font_size=12))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    if height:
        fig.update_layout(height=height)
    return fig


# ----------------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------------
def health_chart(d, color, avg, rul=None, height=320):
    fig = go.Figure()
    fig.add_hrect(y0=0, y1=50, fillcolor="#f87171", opacity=0.07, line_width=0)
    fig.add_hrect(y0=50, y1=70, fillcolor="#fbbf24", opacity=0.07, line_width=0)
    fig.add_hrect(y0=70, y1=100, fillcolor="#34d399", opacity=0.06, line_width=0)
    fig.add_trace(go.Scatter(x=d["time"], y=d["health"], mode="lines", name="Health",
                             line=dict(color=color, width=3), fill="tozeroy",
                             fillcolor="rgba(56,189,248,0.05)",
                             hovertemplate="%{x|%H:%M:%S}<br>Health: %{y:.1f}<extra></extra>"))
    if avg > 1 and len(d) >= avg:
        fig.add_trace(go.Scatter(x=d["time"], y=d["health"].rolling(avg).mean(), mode="lines",
                                 name=f"{avg}-pt average",
                                 line=dict(color="#94a3b8", width=2, dash="dash")))
    last = d.iloc[-1]
    if rul is not None and rul > 0:                       # projected decline to health 50
        horizon = min(rul, 1800)
        y_end = 50 if rul <= 1800 else last["health"] - (last["health"] - 50) * horizon / rul
        fig.add_trace(go.Scatter(
            x=[last["time"], last["time"] + timedelta(seconds=horizon)], y=[last["health"], y_end],
            mode="lines", name="Projected trend", line=dict(color="#fb923c", width=2, dash="dot"),
            hovertemplate="Projected: %{y:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[last["time"]], y=[last["health"]], mode="markers",
                             marker=dict(size=11, color=color, line=dict(width=2, color="white")),
                             showlegend=False, hoverinfo="skip"))
    fig.update_layout(title=dict(text="Health over time", x=0.01), height=height,
                      margin=dict(l=10, r=10, t=50, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
                      xaxis=dict(type="date", tickformat="%H:%M:%S", rangeslider=dict(visible=False)),
                      yaxis=dict(title="Health (0-100)", range=[0, 100]), uirevision="health")
    return themed(fig)


def sensor_chart(d, eq, key, avg, show_limits):
    s = SENSORS[key]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["time"], y=d[key], mode="lines", name=s["label"],
        line=dict(width=2.2, color=ACCENT),
        hovertemplate=f"%{{x|%H:%M:%S}}<br>{s['label']}: %{{y:.2f}} {s['unit']}<extra></extra>"))
    if avg > 1 and len(d) >= avg:
        fig.add_trace(go.Scatter(x=d["time"], y=d[key].rolling(avg).mean(), mode="lines",
                                 name=f"{avg}-pt average",
                                 line=dict(color="#94a3b8", width=2, dash="dash")))
    fig.add_trace(go.Scatter(x=[d["time"].iloc[-1]], y=[d[key].iloc[-1]], mode="markers",
                             marker=dict(size=10, color=ACCENT, line=dict(width=2, color="white")),
                             showlegend=False, hoverinfo="skip"))
    values = d[key].tolist()
    if show_limits:
        for lim in limit_lines(eq, key):
            values.append(lim)
            fig.add_hline(y=lim, line_dash="dash", line_color="#f87171",
                          annotation_text=f"Limit {lim:g}", annotation_position="top left")
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.1 or 1.0
    fig.update_layout(title=dict(text=f"{s['icon']} {s['label']}", x=0.01), height=280,
                      margin=dict(l=10, r=10, t=45, b=10), hovermode="x unified",
                      showlegend=avg > 1,
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
                      xaxis=dict(type="date", tickformat="%H:%M:%S"),
                      yaxis=dict(title=s["unit"], range=[lo - pad, hi + pad]),
                      uirevision=f"{eq}_{key}")
    return themed(fig)


def overlay_chart(d, eq, keys):
    """All chosen sensors on one axis, shown as deviation from nominal (fraction of trip span)."""
    fig = go.Figure()
    for i, key in enumerate(keys):
        s = SENSORS[key]
        dev = (d[key] - nominal_of(eq, key)) / s["span"] * 100
        fig.add_trace(go.Scatter(x=d["time"], y=dev, mode="lines", name=s["label"],
                                 line=dict(width=2, color=PALETTE[i % len(PALETTE)]),
                                 customdata=d[key],
                                 hovertemplate=f"{s['label']}: %{{customdata:.2f}} {s['unit']} "
                                               f"(%{{y:+.0f}}%)<extra></extra>"))
    fig.add_hline(y=0, line_color="#94a3b8", line_dash="dot", annotation_text="Nominal")
    fig.update_layout(title=dict(text="Deviation from nominal (% of trip span)", x=0.01),
                      height=420, margin=dict(l=10, r=10, t=50, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
                      xaxis=dict(type="date", tickformat="%H:%M:%S"),
                      yaxis=dict(title="% of span"), uirevision=f"ov_{eq}")
    return themed(fig)


# ----------------------------------------------------------------------------
# Page setup + sidebar
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Equipment Health Monitor", page_icon="🏭", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
init_db()
sim = get_simulator()
if USE_MQTT:
    try:
        start_mqtt()
    except Exception:
        st.sidebar.error("MQTT broker not reachable. Showing stored/simulated data only.")


def open_equipment(eq_id):
    st.session_state["goto"] = eq_id


def go_overview():
    st.session_state["goto"] = OVERVIEW


st.sidebar.header("⚙️ Controls")
live = st.sidebar.toggle("Live auto-refresh", value=True)
if live:
    refresh_s = st.sidebar.slider("Refresh every (seconds)", 1, 10, 2)
    st_autorefresh(interval=refresh_s * 1000, key="refresh")
toasts = st.sidebar.toggle("Pop-up alerts on status change", value=True)

with st.sidebar.expander("🧪 Simulator", expanded=False):
    sim.running = st.toggle("Simulator running", value=sim.running)
    sim.interval = st.slider("Seconds per reading", 0.5, 5.0, float(sim.interval), 0.5)
    target = st.selectbox("Machine", list(PROFILES))
    fault_type = st.selectbox("Fault type", list(FAULTS),
                              format_func=lambda f: f.replace("_", " ").title())
    b1, b2 = st.columns(2)
    if b1.button("💥 Inject fault", width='stretch'):
        sim.inject(target, fault_type)
        st.toast(f"Fault injected on {target}", icon="💥")
    if b2.button("🛠 Maintenance", width='stretch'):
        sim.maintain(target)
        st.toast(f"Maintenance done on {target}", icon="🛠")
    st.caption("Faults ramp up over ~1-3 min then recover. Maintenance resets wear.")

raw = load_data()
if raw.empty:
    st.title("🏭 Industrial Equipment Health Monitor")
    st.warning("No data yet. Waiting for readings...")
    st.stop()

all_df = score_health(raw)
equipment_ids = sorted(all_df["equipment_id"].unique())
nav_options = [OVERVIEW] + equipment_ids
if "goto" in st.session_state:                       # set by buttons / chart clicks
    st.session_state["nav"] = st.session_state.pop("goto")
if st.session_state.get("nav") not in nav_options:
    st.session_state["nav"] = OVERVIEW
st.sidebar.selectbox("📍 Go to", nav_options, key="nav")
st.sidebar.caption(f"Last update: {time.strftime('%H:%M:%S')}")

latest_by_eq = all_df.groupby("equipment_id").tail(1).set_index("equipment_id")
latest_by_eq["status"] = latest_by_eq["health"].apply(status_from_health)

# Pop-up when a machine gets worse (or recovers)
prev_status = st.session_state.setdefault("prev_status", {})
for _eq, _st in latest_by_eq["status"].items():
    _old = prev_status.get(_eq)
    if toasts and _old and STATUS_GROUP[_old] != STATUS_GROUP[_st]:
        worse = STATUS_RANK[_st] > STATUS_RANK[_old]
        st.toast(f"{_eq} {'degraded to' if worse else 'recovered to'} {_st.title()}",
                 icon="🚨" if worse else "✅")
    prev_status[_eq] = _st

st.markdown(
    f'<div class="hero"><div><div class="hero-title">🏭 Equipment Health Monitor</div>'
    f'<div class="hero-sub">AI condition monitoring across {len(equipment_ids)} machines, '
    f'{len(SENSORS)} sensors each</div></div>'
    f'<div class="live"><span class="dot{"" if live else " off"}"></span>'
    f'{"Live" if live else "Paused"} · {time.strftime("%H:%M:%S")}</div></div>',
    unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Overview page
# ----------------------------------------------------------------------------
def eq_card(eq):
    row, status = latest_by_eq.loc[eq], latest_by_eq.loc[eq, "status"]
    d = all_df[all_df["equipment_id"] == eq]
    hist = d.tail(80)
    prev = hist["health"].iloc[-2] if len(hist) > 1 else row["health"]
    key, pts = top_contributor(row)
    color = STATUS_COLOR[status]
    delta = row["health"] - prev
    with st.container(border=True):
        st.markdown(
            f'<div class="eq-head">{ring_svg(row["health"], color)}<div>'
            f'<div class="eq-name">{eq}</div>{pill(status)}'
            f'<div class="eq-sub">{"▲" if delta >= 0 else "▼"} {abs(delta):.1f} since last reading</div>'
            f'</div></div><div class="eq-stats">'
            + stat("Motor temp", f'{row["temperature"]:.1f} °C')
            + stat("Vibration", f'{row["vibration"]:.2f} mm/s')
            + stat("Current", f'{row["current"]:.1f} A')
            + stat("Time to health 50", fmt_duration(estimate_rul(d)))
            + '</div>'
            + (f'<div class="eq-sub">Main contributor: {SENSORS[key]["label"]} (-{pts:.1f} pts)</div>'
               if pts > 0.5 else '<div class="eq-sub">No sensor is dragging the score down</div>'),
            unsafe_allow_html=True)
        spark = go.Figure(go.Scatter(
            x=hist["time"], y=hist["health"], mode="lines", fill="tozeroy", line=dict(color=color, width=2.5),
            fillcolor="rgba(143,163,187,0.08)",
            hovertemplate="%{x|%H:%M:%S}<br>Health: %{y:.1f}<extra></extra>"))
        spark.update_layout(height=90, margin=dict(l=0, r=0, t=6, b=0),
                            xaxis=dict(visible=False, type="date"), yaxis=dict(visible=False, range=[0, 100]))
        st.plotly_chart(themed(spark), width='stretch', key=f"spark_{eq}",
                        config={"displayModeBar": False})
        st.button("Open details", key=f"open_{eq}", on_click=open_equipment, args=(eq,),
                  width='stretch')


def render_overview():
    avg_health = latest_by_eq["health"].mean()
    groups = latest_by_eq["status"].map(STATUS_GROUP)
    k = st.columns(5)
    k[0].markdown(kpi("Fleet health", f"{avg_health:.0f}/100", "average of all machines",
                      STATUS_COLOR[status_from_health(avg_health)]), unsafe_allow_html=True)
    k[1].markdown(kpi("Machines", len(latest_by_eq), "monitored"), unsafe_allow_html=True)
    k[2].markdown(kpi("Healthy", int((groups == "Healthy").sum()), "health 70 or above", "#34d399"),
                  unsafe_allow_html=True)
    k[3].markdown(kpi("Warning", int((groups == "Warning").sum()), "health 50 to 70", "#fbbf24"),
                  unsafe_allow_html=True)
    k[4].markdown(kpi("Critical", int((groups == "Critical").sum()), "health below 50", "#f87171"),
                  unsafe_allow_html=True)
    st.write("")

    tab_eq, tab_fleet, tab_alerts = st.tabs(["🔧 Equipment", "📊 Fleet analysis", "⚠️ Alerts"])

    with tab_eq:
        f1, f2, f3, f4 = st.columns([2, 3, 2, 2])
        search = f1.text_input("Search", placeholder="Search machine…", label_visibility="collapsed")
        wanted = f2.pills("Status filter", ["Healthy", "Warning", "Critical"], selection_mode="multi",
                          label_visibility="collapsed")
        sort = f3.selectbox("Sort", ["Name", "Health: lowest first", "Health: highest first"],
                            label_visibility="collapsed")
        mode = f4.segmented_control("View", ["Cards", "Table"], default="Cards",
                                    label_visibility="collapsed") or "Cards"

        ids = [e for e in equipment_ids if search.lower() in e.lower()
               and (not wanted or STATUS_GROUP[latest_by_eq.loc[e, "status"]] in wanted)]
        if sort != "Name":
            ids.sort(key=lambda e: latest_by_eq.loc[e, "health"], reverse=sort.endswith("highest first"))

        if not ids:
            st.info("No machines match these filters. Clear the search or status filter to see all.")
        elif mode == "Cards":
            for start in range(0, len(ids), 3):
                cols = st.columns(3)
                for col, eq in zip(cols, ids[start:start + 3]):
                    with col:
                        eq_card(eq)
        else:
            rows = []
            for eq in ids:
                r = latest_by_eq.loc[eq]
                key, pts = top_contributor(r)
                rows.append({"Equipment": eq, "Status": r["status"].title(), "Health": r["health"],
                             "Main contributor": f"{SENSORS[key]['label']} (-{pts:.1f})" if pts > 0.5 else "-",
                             "RUL": fmt_duration(estimate_rul(all_df[all_df["equipment_id"] == eq])),
                             **{SENSORS[k_]["label"]: r[k_] for k_ in SENSORS}, "Time": r["timestamp"]})
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True,
                         column_config={"Health": st.column_config.ProgressColumn(
                             "Health", min_value=0, max_value=100, format="%.0f")})

    with tab_fleet:
        left, right = st.columns(2)
        with left:
            bar = px.bar(latest_by_eq.reset_index(), x="equipment_id", y="health", color="status",
                         color_discrete_map=STATUS_COLOR, text="health",
                         title="Current health (click a bar to open that machine)")
            bar.update_traces(texttemplate="%{text:.0f}", textposition="outside")
            bar.update_yaxes(range=[0, 112], title="Health (0-100)")
            bar.update_xaxes(title="")
            bar.update_layout(margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
            ev = st.plotly_chart(themed(bar, 360), width='stretch', key="fleet_bar",
                                 on_select="rerun", selection_mode="points")
            pts = ev.selection.points if ev and ev.selection else []
            if pts and pts[0].get("x") in equipment_ids:
                st.session_state["goto"] = pts[0]["x"]
                st.rerun()
        with right:
            trend = px.line(all_df.groupby("equipment_id").tail(150), x="time", y="health",
                            color="equipment_id", title="Health trend (all equipment)")
            trend.update_yaxes(range=[0, 100], title="Health (0-100)")
            trend.update_xaxes(title="", tickformat="%H:%M:%S")
            trend.update_layout(hovermode="x unified", uirevision="fleet", legend_title_text="",
                                margin=dict(l=10, r=10, t=50, b=10))
            st.plotly_chart(themed(trend, 360), width='stretch', key="fleet_trend")

        pen = latest_by_eq[[f"pen_{k_}" for k_ in SENSORS]].copy()
        pen.columns = [SENSORS[k_]["label"] for k_ in SENSORS]
        heat = px.imshow(pen.round(1), aspect="auto", color_continuous_scale=["#111c2b", "#fbbf24", "#f87171"],
                         labels=dict(color="Points lost"), title="Where health points are being lost")
        heat.update_layout(margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(themed(heat, 300), width='stretch', key="fleet_heat")

    with tab_alerts:
        any_alert = False
        for eq in sorted(equipment_ids, key=lambda e: latest_by_eq.loc[e, "health"]):
            r = latest_by_eq.loc[eq]
            key, pts = top_contributor(r)
            why = f" Main contributor: <b>{SENSORS[key]['label']}</b> (-{pts:.1f} pts)." if pts > 0.5 else ""
            if r["health"] < 70:
                any_alert = True
                level = "Critical condition" if r["health"] < 50 else "Abnormal behaviour"
                color = "#f87171" if r["health"] < 50 else "#fbbf24"
                c1, c2 = st.columns([6, 1])
                c1.markdown(f'<div class="alert" style="--c:{color}"><div><b>{eq}</b>: {level} '
                            f'(health {r["health"]:.0f}).{why}</div></div>', unsafe_allow_html=True)
                c2.button("Open", key=f"alert_{eq}", on_click=open_equipment, args=(eq,),
                          width='stretch')
        if not any_alert:
            st.markdown('<div class="alert alert-ok" style="--c:#34d399">✅ All equipment is operating '
                        'within the configured health range.</div>', unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Detail page
# ----------------------------------------------------------------------------
def render_detail(eq):
    df = all_df[all_df["equipment_id"] == eq].reset_index(drop=True)
    status = status_from_health(df.iloc[-1]["health"])

    top = st.columns([1, 5, 4])
    top[0].button("⬅ Overview", on_click=go_overview, width='stretch')
    fault = sim.state.get(eq, {}).get("fault")
    top[1].markdown(
        f'<span style="font-size:1.6rem;font-weight:700">{eq}</span> &nbsp;{pill(status)}'
        + (f' &nbsp;<span class="pill" style="--c:#a78bfa">Simulated fault: '
           f'{fault["type"].replace("_", " ")} {fault["elapsed"]}/{fault["duration"]}</span>' if fault else ""),
        unsafe_allow_html=True)
    window = top[2].segmented_control("Window", [30, 60, 120, 300, 600], default=120,
                                      format_func=lambda n: f"Last {n}", key=f"win_{eq}",
                                      label_visibility="collapsed") or 120

    view = df.tail(int(window))
    latest = view.iloc[-1]
    prev = view.iloc[-2] if len(view) > 1 else latest
    top_key, top_pts = top_contributor(latest)
    rul = estimate_rul(df)
    color = STATUS_COLOR[status]

    k = st.columns(5)
    k[0].markdown(kpi("Health", f"{latest['health']:.0f}/100",
                      f"{latest['health'] - prev['health']:+.1f} since last reading", color),
                  unsafe_allow_html=True)
    k[1].markdown(kpi("Motor temp", f"{latest['temperature']:.1f} °C",
                      f"{latest['temperature'] - prev['temperature']:+.1f}", "#fb923c"), unsafe_allow_html=True)
    k[2].markdown(kpi("Vibration", f"{latest['vibration']:.2f} mm/s",
                      f"{latest['vibration'] - prev['vibration']:+.2f}", "#a78bfa"), unsafe_allow_html=True)
    k[3].markdown(kpi("Time to health 50", fmt_duration(rul), "projected from recent trend"),
                  unsafe_allow_html=True)
    k[4].markdown(kpi("Anomalous sensors", f"{latest['anomaly'] * len(SENSORS):.0f} / {len(SENSORS)}",
                      "sudden changes vs recent past", "#f472b6"), unsafe_allow_html=True)
    st.write("")

    t_live, t_sens, t_diag, t_data = st.tabs(["📡 Live", "📈 Sensors", "🧠 Diagnostics", "📋 Data"])

    with t_live:
        avg = st.select_slider("Smooth health with moving average", [0, 3, 5, 10, 20], value=0,
                               format_func=lambda v: "Off" if v == 0 else f"{v} readings", key=f"havg_{eq}")
        g_col, h_col = st.columns([1, 2])
        with g_col:
            gauge = go.Figure(go.Indicator(
                mode="gauge+number", value=float(latest["health"]), title={"text": "Health score"},
                gauge={"axis": {"range": [0, 100]}, "bar": {"color": color, "thickness": 0.28},
                       "bgcolor": "rgba(0,0,0,0)", "borderwidth": 0,
                       "steps": [{"range": [0, 30], "color": "rgba(248,113,113,.28)"},
                                 {"range": [30, 50], "color": "rgba(251,146,60,.25)"},
                                 {"range": [50, 70], "color": "rgba(251,191,36,.22)"},
                                 {"range": [70, 100], "color": "rgba(52,211,153,.20)"}]}))
            gauge.update_layout(margin=dict(l=20, r=20, t=60, b=10))
            st.plotly_chart(themed(gauge, 320), width='stretch', key=f"gauge_{eq}")
        with h_col:
            st.plotly_chart(health_chart(view, color, avg, rul), width='stretch', key=f"health_{eq}")

        st.markdown("##### Active alerts")
        shown = False
        if latest["health"] < 50:
            st.markdown('<div class="alert" style="--c:#f87171"><div><b>Critical equipment condition.</b> '
                        'Schedule maintenance immediately.</div></div>', unsafe_allow_html=True)
            shown = True
        elif latest["health"] < 70:
            st.markdown('<div class="alert" style="--c:#fbbf24"><div><b>Abnormal behaviour detected.</b> '
                        'Inspect the sensors below.</div></div>', unsafe_allow_html=True)
            shown = True
        if top_pts > 0.5:
            st.markdown(f'<div class="alert" style="--c:{ACCENT}"><div>Main contributor: '
                        f'<b>{SENSORS[top_key]["label"]}</b> (-{top_pts:.1f} pts)</div></div>',
                        unsafe_allow_html=True)
            shown = True
        for key, msg in sensor_alerts(eq, latest):
            st.markdown(f'<div class="alert" style="--c:#fbbf24"><div>{SENSORS[key]["icon"]} {msg}</div></div>',
                        unsafe_allow_html=True)
            shown = True
        if not shown:
            st.markdown('<div class="alert alert-ok" style="--c:#34d399">✅ Equipment operating within '
                        'the configured health range.</div>', unsafe_allow_html=True)

    with t_sens:
        tiles = ""
        for key, s in SENSORS.items():
            v = latest[key]
            st_ = sensor_state(eq, key, v)
            frac = min(sensor_dev(eq, key, v) / s["span"], 1) * 100
            tiles += (f'<div class="stile"><div class="stile-h"><span>{s["icon"]} {s["label"]}</span>'
                      f'<span style="color:{SENSOR_COLOR[st_]}">{ {"ok": "Normal", "warn": "Warning", "trip": "Trip"}[st_] }</span></div>'
                      f'<div class="stile-v">{v:.2f}<span class="stile-u">{s["unit"]}</span></div>'
                      f'<div class="bar"><span style="width:{max(frac, 3):.0f}%;background:{SENSOR_COLOR[st_]}"></span></div></div>')
        st.markdown(f'<div class="sgrid">{tiles}</div>', unsafe_allow_html=True)

        c1, c2, c3 = st.columns([5, 2, 2])
        chosen = c1.pills("Sensors to chart", list(SENSORS), selection_mode="multi",
                          default=["temperature", "vibration", "current", "bearing_temp"],
                          format_func=lambda k_: f"{SENSORS[k_]['icon']} {SENSORS[k_]['label']}",
                          key=f"sens_{eq}")
        layout = c2.segmented_control("Layout", ["Separate", "Overlay"], default="Separate",
                                      key=f"lay_{eq}") or "Separate"
        avg = c3.select_slider("Moving average", [0, 3, 5, 10, 20], value=0,
                               format_func=lambda v: "Off" if v == 0 else str(v), key=f"avg_{eq}")
        show_limits = st.checkbox("Show warning limits", True, key=f"lim_{eq}")

        if not chosen:
            st.info("Pick at least one sensor above to chart it.")
        elif layout == "Overlay":
            st.plotly_chart(overlay_chart(view, eq, chosen), width='stretch', key=f"overlay_{eq}")
        else:
            for i in range(0, len(chosen), 2):
                cols = st.columns(2)
                for col, key in zip(cols, chosen[i:i + 2]):
                    with col:
                        st.plotly_chart(sensor_chart(view, eq, key, avg, show_limits),
                                        width='stretch', key=f"{key}_{eq}")

    with t_diag:
        d1, d2 = st.columns(2)
        contrib = pd.DataFrame({"sensor": [SENSORS[k_]["label"] for k_ in SENSORS],
                                "points_lost": [latest[f"pen_{k_}"] for k_ in SENSORS]}
                               ).sort_values("points_lost")
        cb = px.bar(contrib, x="points_lost", y="sensor", orientation="h",
                    title="Why this score? Health points lost per sensor",
                    color="points_lost", color_continuous_scale=["#1f3047", "#fbbf24", "#f87171"])
        cb.update_layout(margin=dict(l=10, r=10, t=50, b=10), coloraxis_showscale=False,
                         uirevision=f"c_{eq}")
        cb.update_xaxes(title="Points lost")
        cb.update_yaxes(title="")
        d1.plotly_chart(themed(cb, 380), width='stretch', key=f"contrib_{eq}")

        corr = view[list(SENSORS)].corr().fillna(0)
        corr.index = corr.columns = [SENSORS[k_]["label"] for k_ in SENSORS]
        cm = px.imshow(corr.round(2), zmin=-1, zmax=1, aspect="auto",
                       color_continuous_scale=["#38bdf8", "#111c2b", "#f87171"],
                       title="Sensor correlation (sensors that move together)")
        cm.update_layout(margin=dict(l=10, r=10, t=50, b=10))
        d2.plotly_chart(themed(cm, 380), width='stretch', key=f"corr_{eq}")

        an = go.Figure(go.Scatter(x=view["time"], y=view["anomaly"] * len(SENSORS), mode="lines",
                                  line=dict(color="#f472b6", width=2, shape="hv"), fill="tozeroy",
                                  fillcolor="rgba(244,114,182,0.15)",
                                  hovertemplate="%{x|%H:%M:%S}<br>%{y:.0f} sensors<extra></extra>"))
        an.update_layout(title=dict(text="Anomaly detector: sensors with a sudden change", x=0.01),
                         margin=dict(l=10, r=10, t=50, b=10), xaxis=dict(type="date", tickformat="%H:%M:%S"),
                         yaxis=dict(title="Sensors", range=[0, len(SENSORS)]), uirevision=f"an_{eq}")
        st.plotly_chart(themed(an, 240), width='stretch', key=f"anom_{eq}")

    with t_data:
        show = view.drop(columns=["time"] + [c for c in view.columns if c.startswith("pen_")])
        st.dataframe(show.iloc[::-1], width='stretch', hide_index=True)
        st.download_button("⬇ Download CSV", show.to_csv(index=False),
                           file_name=f"{eq}_data.csv", mime="text/csv")


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------
if st.session_state["nav"] == OVERVIEW:
    render_overview()
else:
    render_detail(st.session_state["nav"])