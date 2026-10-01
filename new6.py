import json
import os
import sqlite3
import threading
import time

import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

try:  # scipy gives us shape-preserving and cubic-spline interpolation
    from scipy.interpolate import CubicSpline, PchipInterpolator
    SCIPY_OK = True
except ImportError:  # polynomial fit still works with numpy only
    SCIPY_OK = False


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
BROKER_HOST = "localhost"
BROKER_PORT = 1883
MQTT_TOPIC = "test/topic"
DB_PATH = "local.db"
TABLE_NAME = "equipment_data"

# Warning limits used for the reference lines on the detail charts
THRESHOLDS = {
    "temperature": 80.0,
    "vibration": 4.5,
    "current": 40.0,
}

SENSORS = {
    "temperature": {"label": "Temperature", "unit": "°C", "icon": "🌡"},
    "vibration": {"label": "Vibration", "unit": "mm/s", "icon": "📳"},
    "current": {"label": "Current", "unit": "A", "icon": "⚡"},
    "rpm": {"label": "RPM", "unit": "rpm", "icon": "🔄"},
    "pressure": {"label": "Pressure", "unit": "bar", "icon": "🧭"},
}

OVERVIEW = "🏠 Overview (all equipment)"

_db_lock = threading.Lock()


# ----------------------------------------------------------------------------
# Database  (created ONLY if it does not already exist)
# ----------------------------------------------------------------------------
def database_is_ready():
    """True if the DB file exists AND already contains the equipment table."""
    if not os.path.exists(DB_PATH):
        return False
    connection = sqlite3.connect(DB_PATH)
    try:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_NAME,),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def ensure_database():
    """Create the database/table + sample rows only when they are missing."""
    if database_is_ready():
        return  # already present -> do nothing

    with _db_lock:
        if database_is_ready():  # re-check in case another thread just made it
            return

        connection = sqlite3.connect(DB_PATH)
        try:
            connection.execute(
                f"""
                CREATE TABLE {TABLE_NAME} (
                    equipment_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    temperature REAL NOT NULL,
                    vibration REAL NOT NULL,
                    current REAL NOT NULL,
                    rpm REAL NOT NULL,
                    pressure REAL NOT NULL,
                    health_score REAL NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                f"""
                INSERT INTO {TABLE_NAME}
                (equipment_id, timestamp, temperature, vibration, current,
                 rpm, pressure, health_score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("MOTOR-01", "2026-09-29 09:00", 62.5, 2.1, 12.4, 1450, 3.2, 92, "NORMAL"),
                    ("MOTOR-01", "2026-09-29 10:00", 64.0, 2.4, 12.8, 1445, 3.1, 88, "GOOD"),
                    ("PUMP-01", "2026-09-29 09:00", 71.2, 3.8, 15.1, 1380, 2.8, 68, "WARNING"),
                    ("PUMP-01", "2026-09-29 10:00", 73.5, 4.2, 15.6, 1372, 2.7, 61, "WARNING"),
                    ("COMPRESSOR-01", "2026-09-29 09:00", 58.4, 1.7, 10.8, 1620, 4.1, 95, "NORMAL"),
                    ("COMPRESSOR-01", "2026-09-29 10:00", 59.1, 1.8, 10.9, 1618, 4.0, 94, "NORMAL"),
                ],
            )
            connection.commit()
            print(f"[+] Database '{DB_PATH}' was missing -> created with sample data")
        finally:
            connection.close()


def get_connection():
    ensure_database()
    return sqlite3.connect(DB_PATH)


def get_all_data():
    connection = get_connection()
    try:
        df = pd.read_sql(
            f"SELECT * FROM {TABLE_NAME} ORDER BY equipment_id, timestamp",
            connection,
        )
    finally:
        connection.close()
    # Clean data so every chart is mathematically consistent
    df["time"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for column in ["temperature", "vibration", "current", "rpm", "pressure", "health_score"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["time", "temperature", "vibration", "current",
                           "rpm", "pressure", "health_score"])
    df = df.drop_duplicates(subset=["equipment_id", "time"], keep="last")
    df = df.sort_values(["equipment_id", "time"]).reset_index(drop=True)  # true time order
    return df


# ----------------------------------------------------------------------------
# Health / status helpers
# ----------------------------------------------------------------------------
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


STATUS_ICON = {
    "NORMAL": "🟢",
    "GOOD": "🟢",
    "WARNING": "🟡",
    "CRITICAL": "🟠",
    "SEVERE": "🔴",
}

STATUS_COLOR = {
    "NORMAL": "#2ecc71",
    "GOOD": "#7bd88f",
    "WARNING": "#f1c40f",
    "CRITICAL": "#e67e22",
    "SEVERE": "#e74c3c",
}


# ----------------------------------------------------------------------------
# MQTT
# ----------------------------------------------------------------------------
def save_mqtt_reading(payload):
    required_fields = {
        "equipment_id",
        "timestamp",
        "temperature",
        "vibration",
        "current",
        "rpm",
        "pressure",
    }
    if not required_fields.issubset(payload):
        return

    temperature = float(payload["temperature"])
    vibration = float(payload["vibration"])
    current = float(payload["current"])
    rpm = float(payload["rpm"])
    pressure = float(payload["pressure"])
    health_score = payload.get("health_score")

    if health_score is None:
        health_score = max(
            0,
            min(
                100,
                100
                - max(0, temperature - 75) * 2
                - vibration * 3
                - max(0, current - 40)
                - abs(rpm - 1500) / 20
                - abs(pressure - 3.5) * 5,
            ),
        )

    health_score = float(health_score)
    status = status_from_health(health_score)

    connection = get_connection()
    try:
        connection.execute(
            f"""
            INSERT INTO {TABLE_NAME}
            (equipment_id, timestamp, temperature, vibration, current,
             rpm, pressure, health_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["equipment_id"],
                payload["timestamp"],
                temperature,
                vibration,
                current,
                rpm,
                pressure,
                health_score,
                status,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[+] Connected to Mosquitto broker at {BROKER_HOST}:{BROKER_PORT}")
        client.subscribe(MQTT_TOPIC)
        print(f"[*] Subscribed to topic: '{MQTT_TOPIC}'")
    else:
        print(f"[-] MQTT connection failed with reason code: {reason_code}")


def on_mqtt_message(client, userdata, message):
    payload = message.payload.decode("utf-8")
    try:
        payload = json.loads(payload)
    except json.JSONDecodeError:
        pass

    if isinstance(payload, dict):
        try:
            save_mqtt_reading(payload)
        except Exception as exc:  # keep the subscriber alive on bad payloads
            print(f"[!] Could not save reading: {exc}")
    print(f"[Message Received] Topic: '{message.topic}' | Payload: {payload}")


@st.cache_resource
def connect_mqtt():
    """Connect to the local broker and return the running MQTT client."""
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_mqtt_connect
    client.on_message = on_mqtt_message
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    return client


if __name__ == "__main__" and not st.runtime.exists():
    try:
        ensure_database()
        mqtt_client = connect_mqtt()
        print("[*] Waiting for MQTT messages. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except ConnectionRefusedError:
        print("[!] MQTT connection refused. Start Mosquitto with: mosquitto -c mosquitto.conf -v")
    except KeyboardInterrupt:
        print("\n[*] Stopping MQTT subscriber...")
    finally:
        if "mqtt_client" in locals():
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


# ----------------------------------------------------------------------------
# Precise chart helpers
# ----------------------------------------------------------------------------
def padded_range(values, floor=None, ceil=None, extra=None):
    """Y-axis range with 12% padding so data never touches the edges.

    `floor` / `ceil` clamp physical limits (e.g. sensors can't go below 0),
    `extra` lets reference lines (limits) stay inside the visible range.
    """
    values = [float(v) for v in values if pd.notna(v)]
    if extra is not None:
        values.append(float(extra))
    if not values:
        return None
    low, high = min(values), max(values)
    span = high - low
    pad = span * 0.12 if span > 0 else max(abs(high) * 0.05, 1.0)
    low, high = low - pad, high + pad
    if floor is not None:
        low = max(low, floor)
    if ceil is not None:
        high = min(high, ceil)
    return [low, high]


CURVE_LINES = "Exact lines (no curve)"
CURVE_PCHIP = "Smooth curve - PCHIP (no overshoot)"
CURVE_SPLINE = "Cubic spline (passes through samples)"
CURVE_POLY = "Polynomial fit (least squares)"

if SCIPY_OK:
    CURVE_MODES = [CURVE_LINES, CURVE_PCHIP, CURVE_SPLINE, CURVE_POLY]
else:
    CURVE_MODES = [CURVE_LINES, CURVE_POLY]


def poly_equation(coefficients):
    """Readable equation. u = 0 at the first reading, u = 1 at the last."""
    degree = len(coefficients) - 1
    terms = []
    for index, coefficient in enumerate(coefficients):
        power = degree - index
        if power == 0:
            body = f"{abs(coefficient):.4g}"
        elif power == 1:
            body = f"{abs(coefficient):.4g}·u"
        else:
            body = f"{abs(coefficient):.4g}·u^{power}"
        terms.append(("-" if coefficient < 0 else "+", body))
    text = ("-" if terms[0][0] == "-" else "") + terms[0][1]
    for sign, body in terms[1:]:
        text += f" {sign} {body}"
    return "y = " + text


def compute_curve(data, column, mode, degree=3, sample_n=0, clip=None, points=400):
    """Build a mathematical curve from sampled readings.

    1. Take sample values (all readings, or N evenly spaced ones).
    2. Fit/interpolate a function y = f(u) through them, where u is the
       normalised time (0 = first reading, 1 = last reading).
    3. Evaluate f on a dense grid so the curve is truly continuous.
    4. Measure how far the curve is from ALL real readings (R2 and RMSE).
    """
    if mode == CURVE_LINES:
        return None

    work = data[["time", column]].dropna().reset_index(drop=True)
    if len(work) < 3:
        return None

    t0 = work["time"].iloc[0]
    span = (work["time"].iloc[-1] - t0).total_seconds()
    if span <= 0:
        return None

    u_all = ((work["time"] - t0).dt.total_seconds() / span).to_numpy()
    y_all = work[column].to_numpy(dtype=float)

    # Step 1 - sample values (evenly spaced across the window)
    if sample_n and sample_n < len(work):
        idx = np.unique(np.linspace(0, len(work) - 1, int(sample_n)).round().astype(int))
    else:
        idx = np.arange(len(work))
    if len(idx) < 3:
        return None
    u_s, y_s = u_all[idx], y_all[idx]

    # Step 2 - build f(u)
    equation = None
    if mode == CURVE_POLY:
        used_degree = int(min(degree, len(idx) - 1))
        coefficients = np.polyfit(u_s, y_s, used_degree)
        f = np.poly1d(coefficients)
        equation = poly_equation(coefficients)
        label = f"Polynomial fit (degree {used_degree})"
    elif mode == CURVE_SPLINE and SCIPY_OK:
        f = CubicSpline(u_s, y_s, bc_type="natural")
        label = "Cubic spline"
    elif SCIPY_OK:
        f = PchipInterpolator(u_s, y_s)
        label = "PCHIP curve"
    else:
        return None

    # Step 3 - dense, continuous curve
    u_dense = np.linspace(0.0, 1.0, points)
    y_dense = np.asarray(f(u_dense), dtype=float)
    if clip is not None:
        y_dense = np.clip(y_dense, clip[0], clip[1])
    t_dense = t0 + pd.to_timedelta(u_dense * span, unit="s")

    # Step 4 - accuracy against every real reading
    predicted = np.asarray(f(u_all), dtype=float)
    residual = y_all - predicted
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((y_all - y_all.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return {
        "time": t_dense,
        "y": y_dense,
        "sample_time": work["time"].iloc[idx],
        "sample_y": y_s,
        "n_samples": int(len(idx)),
        "n_total": int(len(work)),
        "label": label,
        "equation": equation,
        "rmse": rmse,
        "r2": r2,
    }


def show_curve_info(curve):
    """Show how the curve was built and how accurate it is."""
    if not curve:
        return
    st.caption(
        f"📐 {curve['label']} · built from {curve['n_samples']} of "
        f"{curve['n_total']} readings · R² = {curve['r2']:.4f} · "
        f"RMSE = {curve['rmse']:.3f}"
    )
    if curve["equation"]:
        st.caption(f"{curve['equation']}   (u = 0 at first reading, 1 at last reading shown)")


def build_line_chart(
    data,
    column,
    title,
    label,
    unit="",
    color="#1f77b4",
    limit=None,
    y_range=None,
    curve=None,
    moving_avg=0,
    health_bands=False,
):
    """Time-series chart: true time axis, units, exact hover values,
    min/max markers, optional limit line, moving average and health bands."""
    fig = go.Figure()

    if health_bands:  # colour zones match the status thresholds exactly
        fig.add_hrect(y0=0, y1=50, fillcolor="#e74c3c", opacity=0.08, line_width=0)
        fig.add_hrect(y0=50, y1=70, fillcolor="#f1c40f", opacity=0.10, line_width=0)
        fig.add_hrect(y0=70, y1=100, fillcolor="#2ecc71", opacity=0.08, line_width=0)

    hover_reading = (
        "%{x|%d %b %Y, %H:%M}<br>"
        f"{label}: %{{y:.2f}} {unit}<extra></extra>"
    )

    if curve:
        # Real readings as points, mathematical curve as a continuous line
        fig.add_trace(
            go.Scatter(
                x=data["time"],
                y=data[column],
                mode="markers",
                name="Readings",
                marker=dict(size=7, color=color, opacity=0.55),
                hovertemplate=hover_reading,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=curve["time"],
                y=curve["y"],
                mode="lines",
                name=curve["label"],
                line=dict(color=color, width=3, shape="linear"),  # dense grid -> smooth
                hovertemplate=f"Curve: %{{y:.2f}} {unit}<extra></extra>",
            )
        )
        if curve["n_samples"] < curve["n_total"]:  # highlight the sampled points
            fig.add_trace(
                go.Scatter(
                    x=curve["sample_time"],
                    y=curve["sample_y"],
                    mode="markers",
                    name="Samples used",
                    marker=dict(size=11, symbol="diamond", color="white",
                                line=dict(width=2, color=color)),
                    hoverinfo="skip",
                )
            )
    else:
        fig.add_trace(
            go.Scatter(
                x=data["time"],
                y=data[column],
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2.5, shape="linear"),
                marker=dict(size=7),
                hovertemplate=hover_reading,
            )
        )

    # Moving average (simple rolling mean over N readings)
    if moving_avg and moving_avg > 1 and len(data) >= moving_avg:
        average = data[column].rolling(window=moving_avg, min_periods=moving_avg).mean()
        fig.add_trace(
            go.Scatter(
                x=data["time"],
                y=average,
                mode="lines",
                name=f"{moving_avg}-pt average",
                line=dict(color="#7f7f7f", width=2, dash="dash"),
                hovertemplate=f"Average: %{{y:.2f}} {unit}<extra></extra>",
            )
        )

    # Mark exact min and max points
    if len(data) > 2:
        high_row = data.loc[data[column].idxmax()]
        low_row = data.loc[data[column].idxmin()]
        fig.add_trace(
            go.Scatter(
                x=[high_row["time"], low_row["time"]],
                y=[high_row[column], low_row[column]],
                mode="markers+text",
                text=[f"max {high_row[column]:.2f}", f"min {low_row[column]:.2f}"],
                textposition=["top center", "bottom center"],
                marker=dict(size=11, color=["#e74c3c", "#3498db"],
                            line=dict(width=1, color="white")),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    if limit is not None:
        fig.add_hline(
            y=limit,
            line_dash="dash",
            line_color="red",
            annotation_text=f"Limit {limit:g} {unit}",
            annotation_position="top left",
        )

    fig.update_layout(
        title=dict(text=title, x=0.01),
        height=340,
        margin=dict(l=10, r=10, t=55, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
        xaxis=dict(
            title="Time",
            type="date",                      # real time scale, not text categories
            tickformat="%d %b\n%H:%M",
            showgrid=True,
            gridcolor="rgba(128,128,128,0.2)",
        ),
        yaxis=dict(
            title=f"{label} ({unit})" if unit else label,
            range=y_range,
            showgrid=True,
            gridcolor="rgba(128,128,128,0.2)",
            zeroline=False,
        ),
    )
    return fig


# ----------------------------------------------------------------------------
# Navigation helpers
# ----------------------------------------------------------------------------
def open_equipment(equipment_id):
    """Callback: jump to the detail page of one machine."""
    st.session_state["nav"] = equipment_id


def go_overview():
    """Callback: go back to the all-equipment page."""
    st.session_state["nav"] = OVERVIEW


# ----------------------------------------------------------------------------
# Page setup
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Equipment Health Monitor",
    page_icon="🏭",
    layout="wide",
)

ensure_database()  # creates DB only if it is not already there

if st.runtime.exists():
    try:
        connect_mqtt()
    except ConnectionRefusedError:
        st.sidebar.error("MQTT broker not reachable. Dashboard is showing stored data only.")

# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
st.sidebar.header("⚙️ Controls")

auto_refresh = st.sidebar.toggle("Live auto-refresh", value=True)
refresh_seconds = st.sidebar.slider("Refresh every (seconds)", 1, 30, 3)
if auto_refresh:
    st_autorefresh(interval=refresh_seconds * 1000, key="mqtt_refresh")

all_df = get_all_data()

if all_df.empty:
    st.title("🏭 Industrial Equipment Health Monitor")
    st.warning("No data available yet.")
    st.stop()

equipment_ids = sorted(all_df["equipment_id"].unique())
nav_options = [OVERVIEW] + equipment_ids

if "nav" not in st.session_state or st.session_state["nav"] not in nav_options:
    st.session_state["nav"] = OVERVIEW

st.sidebar.selectbox("📍 Go to", nav_options, key="nav")

if st.sidebar.button("🔄 Refresh now", use_container_width=True):
    st.rerun()

st.sidebar.caption(f"Last update: {time.strftime('%H:%M:%S')}")

# Latest row + previous row per equipment
latest_by_eq = all_df.groupby("equipment_id").tail(1).set_index("equipment_id")
latest_by_eq["status"] = latest_by_eq["health_score"].apply(status_from_health)

st.title("🏭 Industrial Equipment Health Monitor")


# ----------------------------------------------------------------------------
# OVERVIEW PAGE  (everything on one page)
# ----------------------------------------------------------------------------
def render_overview():
    st.caption("All equipment at a glance. Click **Open details** on any card to drill down.")

    # Fleet summary
    statuses = latest_by_eq["status"]
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Total Equipment", len(latest_by_eq))
    s2.metric("Healthy", int(statuses.isin(["NORMAL", "GOOD"]).sum()))
    s3.metric("Warning", int((statuses == "WARNING").sum()))
    s4.metric("Critical / Severe", int(statuses.isin(["CRITICAL", "SEVERE"]).sum()))

    st.divider()

    # Equipment cards
    st.subheader("🔧 Equipment")
    cards_per_row = 3
    for start in range(0, len(equipment_ids), cards_per_row):
        cols = st.columns(cards_per_row)
        for col, eq_id in zip(cols, equipment_ids[start:start + cards_per_row]):
            row = latest_by_eq.loc[eq_id]
            status = row["status"]
            history = all_df[all_df["equipment_id"] == eq_id]

            with col, st.container(border=True):
                st.markdown(f"### {STATUS_ICON[status]} {eq_id}")
                st.caption(f"Status: **{status}**  •  Updated: {row['timestamp']}")

                m1, m2 = st.columns(2)
                m1.metric("Health", f"{row['health_score']:.0f}/100")
                m2.metric("Temp", f"{row['temperature']:.1f} °C")
                m3, m4 = st.columns(2)
                m3.metric("Vibration", f"{row['vibration']:.2f} mm/s")
                m4.metric("Current", f"{row['current']:.1f} A")

                spark = go.Figure(
                    go.Scatter(
                        x=history["time"],
                        y=history["health_score"],
                        mode="lines+markers",
                        line=dict(color=STATUS_COLOR[status], width=2.5),
                        marker=dict(size=5),
                        hovertemplate="%{x|%d %b, %H:%M}<br>Health: %{y:.1f}<extra></extra>",
                    )
                )
                spark.update_layout(
                    height=110,
                    margin=dict(l=0, r=0, t=0, b=0),
                    xaxis=dict(visible=False, type="date"),
                    yaxis=dict(visible=False, range=[0, 100]),
                )
                st.plotly_chart(
                    spark,
                    use_container_width=True,
                    key=f"spark_{eq_id}",
                    config={"displayModeBar": False},
                )

                st.button(
                    "🔍 Open details",
                    key=f"open_{eq_id}",
                    on_click=open_equipment,
                    args=(eq_id,),
                    use_container_width=True,
                )

    st.divider()

    # Fleet comparison
    st.subheader("📊 Fleet Comparison")
    left, right = st.columns(2)

    with left:
        bar = px.bar(
            latest_by_eq.reset_index(),
            x="equipment_id",
            y="health_score",
            color="status",
            color_discrete_map=STATUS_COLOR,
            text="health_score",
            title="Current Health Score",
        )
        bar.update_traces(
            texttemplate="%{text:.1f}",
            textposition="outside",
            hovertemplate="%{x}<br>Health: %{y:.1f}<extra></extra>",
        )
        bar.update_yaxes(range=[0, 110], title="Health score (0-100)",
                         gridcolor="rgba(128,128,128,0.2)")
        bar.update_xaxes(title="Equipment")
        st.plotly_chart(bar, use_container_width=True, key="fleet_bar")

    with right:
        trend = px.line(
            all_df,
            x="time",
            y="health_score",
            color="equipment_id",
            markers=True,
            title="Health Trend (all equipment)",
        )
        trend.update_traces(hovertemplate="%{x|%d %b, %H:%M}<br>Health: %{y:.1f}<extra></extra>")
        trend.update_yaxes(range=[0, 100], title="Health score (0-100)",
                           gridcolor="rgba(128,128,128,0.2)")
        trend.update_xaxes(title="Time", type="date", tickformat="%d %b\n%H:%M",
                           gridcolor="rgba(128,128,128,0.2)")
        trend.update_layout(hovermode="x unified")
        st.plotly_chart(trend, use_container_width=True, key="fleet_trend")

    # Fleet table
    st.subheader("📋 Latest Readings")
    table = latest_by_eq.reset_index()[
        ["equipment_id", "status", "health_score", "temperature",
         "vibration", "current", "rpm", "pressure", "timestamp"]
    ]
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "health_score": st.column_config.ProgressColumn(
                "Health", min_value=0, max_value=100, format="%.0f"
            ),
        },
    )

    # Fleet alerts
    st.subheader("⚠️ Alerts")
    any_alert = False
    for eq_id in equipment_ids:
        score = latest_by_eq.loc[eq_id, "health_score"]
        if score < 50:
            st.error(f"**{eq_id}**: critical condition (health {score:.0f}). Maintenance should be investigated.")
            any_alert = True
        elif score < 70:
            st.warning(f"**{eq_id}**: abnormal behavior detected (health {score:.0f}).")
            any_alert = True
    if not any_alert:
        st.success("All equipment is operating within the configured health range.")


# ----------------------------------------------------------------------------
# DETAIL PAGE  (one machine)
# ----------------------------------------------------------------------------
def render_detail(eq_id):
    top_left, top_right = st.columns([1, 5])
    top_left.button("⬅ Back to overview", on_click=go_overview)

    df = all_df[all_df["equipment_id"] == eq_id].reset_index(drop=True)
    status = status_from_health(df.iloc[-1]["health_score"])
    st.header(f"{STATUS_ICON[status]} {eq_id}  —  {status}")

    # --- Interactive controls ---
    c1, c2 = st.columns([1, 2])
    with c1:
        window = st.select_slider(
            "Show last N readings",
            options=[5, 10, 25, 50, 100, "All"],
            value="All",
            key=f"window_{eq_id}",
        )
    with c2:
        chosen = st.multiselect(
            "Sensors to chart",
            options=list(SENSORS.keys()),
            default=["temperature", "vibration", "current", "rpm"],
            format_func=lambda k: f"{SENSORS[k]['icon']} {SENSORS[k]['label']}",
            key=f"sensors_{eq_id}",
        )
    o1, o2 = st.columns(2)
    show_limits = o1.checkbox("Show warning limits", value=True, key=f"limits_{eq_id}")
    moving_avg = o2.select_slider(
        "Moving average (readings)",
        options=[0, 3, 5, 10],
        value=0,
        format_func=lambda v: "Off" if v == 0 else str(v),
        key=f"avg_{eq_id}",
    )

    view = df if window == "All" else df.tail(int(window))

    # --- Curve controls: build a mathematical curve from sample values ---
    cc1, cc2, cc3 = st.columns(3)
    curve_mode = cc1.selectbox(
        "Curve type",
        CURVE_MODES,
        index=1 if len(CURVE_MODES) > 2 else 0,
        key=f"curve_{eq_id}",
        help="PCHIP / spline pass exactly through the sample points. "
             "Polynomial fit finds the best-fit equation through them.",
    )
    degree = 3
    if curve_mode == CURVE_POLY:
        degree = cc2.slider("Polynomial degree", 1, 6, 3, key=f"degree_{eq_id}")
    sample_options = ["All"] + [n for n in (5, 10, 20, 50) if n < len(view)]
    sample_choice = cc3.select_slider(
        "Samples used for curve",
        options=sample_options,
        value="All",
        key=f"samples_{eq_id}",
        help="Number of evenly spaced readings the curve is built from.",
    )
    sample_n = 0 if sample_choice == "All" else int(sample_choice)
    if not SCIPY_OK:
        st.info("Install scipy (`pip install scipy`) to unlock PCHIP and cubic spline curves.")
    if len(view) < 3 and curve_mode != CURVE_LINES:
        st.caption("A curve needs at least 3 readings; showing exact lines for now.")

    view = df if window == "All" else df.tail(int(window))
    latest = view.iloc[-1]
    previous = view.iloc[-2] if len(view) > 1 else latest

    # --- KPI row with change since previous reading ---
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Health", f"{latest['health_score']:.0f}/100",
              f"{latest['health_score'] - previous['health_score']:+.0f}")
    k2.metric("Temperature", f"{latest['temperature']:.1f} °C",
              f"{latest['temperature'] - previous['temperature']:+.1f}", delta_color="inverse")
    k3.metric("Vibration", f"{latest['vibration']:.2f} mm/s",
              f"{latest['vibration'] - previous['vibration']:+.2f}", delta_color="inverse")
    k4.metric("Current", f"{latest['current']:.2f} A",
              f"{latest['current'] - previous['current']:+.2f}", delta_color="inverse")
    k5.metric("RPM", f"{latest['rpm']:.0f}",
              f"{latest['rpm'] - previous['rpm']:+.0f}")

    # --- Gauge + health history ---
    g_col, h_col = st.columns([1, 2])
    with g_col:
        gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=float(latest["health_score"]),
                title={"text": "Health Score"},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": STATUS_COLOR[status]},
                    "steps": [
                        {"range": [0, 30], "color": "#f5b7b1"},
                        {"range": [30, 50], "color": "#fad7a0"},
                        {"range": [50, 70], "color": "#f9e79f"},
                        {"range": [70, 100], "color": "#abebc6"},
                    ],
                },
            )
        )
        gauge.update_layout(height=300, margin=dict(l=20, r=20, t=60, b=10))
        st.plotly_chart(gauge, use_container_width=True, key=f"gauge_{eq_id}")

    with h_col:
        health_curve = compute_curve(
            view, "health_score", curve_mode, degree=degree,
            sample_n=sample_n, clip=(0, 100),
        )
        fig_health = build_line_chart(
            view,
            column="health_score",
            title="Equipment Health Over Time",
            label="Health",
            unit="/100",
            color=STATUS_COLOR[status],
            y_range=[0, 100],          # fixed true scale: 0-100
            curve=health_curve,
            moving_avg=moving_avg,
            health_bands=True,         # green / yellow / red zones match status rules
        )
        fig_health.update_layout(height=300)
        st.plotly_chart(fig_health, use_container_width=True, key=f"health_{eq_id}")
        show_curve_info(health_curve)

    # --- Sensor charts (two per row) ---
    st.subheader("📈 Sensor History")
    if not chosen:
        st.info("Select at least one sensor above to see its chart.")
    for i in range(0, len(chosen), 2):
        cols = st.columns(2)
        for col, sensor in zip(cols, chosen[i:i + 2]):
            meta = SENSORS[sensor]
            limit = THRESHOLDS.get(sensor) if show_limits else None
            curve = compute_curve(
                view, sensor, curve_mode, degree=degree,
                sample_n=sample_n, clip=(0, None),   # sensors can't be negative
            )
            values = view[sensor].tolist() + (curve["y"].tolist() if curve else [])
            fig = build_line_chart(
                view,
                column=sensor,
                title=f"{meta['icon']} {meta['label']}",
                label=meta["label"],
                unit=meta["unit"],
                limit=limit,
                y_range=padded_range(values, floor=0, extra=limit),
                curve=curve,
                moving_avg=moving_avg,
            )
            with col:
                st.plotly_chart(fig, use_container_width=True, key=f"{sensor}_{eq_id}")
                show_curve_info(curve)

    # --- Alerts ---
    st.subheader("⚠️ AI Alerts")
    health = latest["health_score"]
    if health < 50:
        st.error("Critical equipment condition. Maintenance should be investigated.")
    elif health < 70:
        st.warning("Abnormal equipment behavior detected.")
    else:
        st.success("Equipment operating within the configured health range.")

    for sensor, limit in THRESHOLDS.items():
        if latest[sensor] > limit:
            meta = SENSORS[sensor]
            st.warning(f"{meta['label']} is {latest[sensor]:.2f} {meta['unit']}, above the limit of {limit} {meta['unit']}.")

    # --- Raw data + download ---
    with st.expander("📋 Raw data"):
        st.dataframe(
            view.drop(columns=["time"]).iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "⬇ Download CSV",
            view.drop(columns=["time"]).to_csv(index=False),
            file_name=f"{eq_id}_data.csv",
            mime="text/csv",
        )


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------
if st.session_state["nav"] == OVERVIEW:
    render_overview()
else:
    render_detail(st.session_state["nav"])