import json
import os
import sqlite3
import threading
import time

import pandas as pd
import paho.mqtt.client as mqtt
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh


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
    df["time"] = pd.to_datetime(df["timestamp"], errors="coerce")
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

                spark = px.line(history, x="time", y="health_score", markers=False)
                spark.update_traces(line_color=STATUS_COLOR[status])
                spark.update_layout(
                    height=110,
                    margin=dict(l=0, r=0, t=0, b=0),
                    xaxis=dict(visible=False),
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
        bar.update_traces(texttemplate="%{text:.0f}", textposition="outside")
        bar.update_yaxes(range=[0, 110])
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
        trend.update_yaxes(range=[0, 100])
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
    show_limits = st.checkbox("Show warning limits on charts", value=True, key=f"limits_{eq_id}")

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
        fig_health = px.line(
            view, x="time", y="health_score", markers=True,
            title="Equipment Health Over Time",
        )
        fig_health.update_yaxes(range=[0, 100])
        fig_health.add_hline(y=70, line_dash="dot", line_color="orange",
                             annotation_text="Warning below 70")
        fig_health.add_hline(y=50, line_dash="dot", line_color="red",
                             annotation_text="Critical below 50")
        fig_health.update_layout(height=300)
        st.plotly_chart(fig_health, use_container_width=True, key=f"health_{eq_id}")

    # --- Sensor charts (two per row) ---
    st.subheader("📈 Sensor History")
    if not chosen:
        st.info("Select at least one sensor above to see its chart.")
    for i in range(0, len(chosen), 2):
        cols = st.columns(2)
        for col, sensor in zip(cols, chosen[i:i + 2]):
            meta = SENSORS[sensor]
            fig = px.line(
                view, x="time", y=sensor, markers=True,
                title=f"{meta['icon']} {meta['label']} ({meta['unit']})",
            )
            if show_limits and sensor in THRESHOLDS:
                fig.add_hline(y=THRESHOLDS[sensor], line_dash="dash", line_color="red",
                              annotation_text="Limit")
            col.plotly_chart(fig, use_container_width=True, key=f"{sensor}_{eq_id}")

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