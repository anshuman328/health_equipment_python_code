import sqlite3
import json
import time

import pandas as pd
import paho.mqtt.client as mqtt
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh


BROKER_HOST = "localhost"
BROKER_PORT = 1883
MQTT_TOPIC = "test/topic"


def get_connection():
    return sqlite3.connect("local.db")


def initialize_database(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS equipment_data (
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

    row_count = connection.execute(
        "SELECT COUNT(*) FROM equipment_data"
    ).fetchone()[0]

    if row_count == 0:
        connection.executemany(
            """
            INSERT INTO equipment_data
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


def get_equipment_data(equipment_id):
    connection = get_connection()
    initialize_database(connection)

    query = """
        SELECT
            timestamp,
            temperature,
            vibration,
            current,
            rpm,
            pressure,
            health_score,
            status
        FROM equipment_data
        WHERE equipment_id = ?
        ORDER BY timestamp
    """

    df = pd.read_sql(query, connection, params=(equipment_id,))

    connection.close()

    return df


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
    if health_score >= 90:
        status = "NORMAL"
    elif health_score >= 70:
        status = "GOOD"
    elif health_score >= 50:
        status = "WARNING"
    elif health_score >= 30:
        status = "CRITICAL"
    else:
        status = "SEVERE"

    connection = get_connection()
    initialize_database(connection)
    connection.execute(
        """
        INSERT INTO equipment_data
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
        save_mqtt_reading(payload)
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
st.set_page_config(
    page_title="Equipment Health Monitor",
    page_icon="🏭",
    layout="wide"
)

st_autorefresh(interval=3000, key="mqtt_refresh")

if st.runtime.exists():
    connect_mqtt()

st.title("🏭 Industrial Equipment Health Monitor")

# Sidebar
st.sidebar.header("Equipment")

equipment_id = st.sidebar.selectbox(
    "Select Equipment",
    ["MOTOR-01", "PUMP-01", "COMPRESSOR-01"]
)

# Get database data
df = get_equipment_data(equipment_id)

if df.empty:
    st.warning("No data available.")
    st.stop()

# Latest reading
latest = df.iloc[-1]

health = latest["health_score"]
temperature = latest["temperature"]
vibration = latest["vibration"]
current = latest["current"]

# Status
if health >= 90:
    status = "🟢 NORMAL"
elif health >= 70:
    status = "🟢 GOOD"
elif health >= 50:
    status = "🟡 WARNING"
elif health >= 30:
    status = "🟠 CRITICAL"
else:
    status = "🔴 SEVERE"

# KPI cards
col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "Health Score",
    f"{health:.0f}/100"
)

col2.metric(
    "Temperature",
    f"{temperature:.1f} °C"
)

col3.metric(
    "Vibration",
    f"{vibration:.2f} mm/s"
)

col4.metric(
    "Current",
    f"{current:.2f} A"
)

st.subheader(f"Equipment Status: {status}")

# Health graph
st.subheader("📈 Health Score History")

fig_health = px.line(
    df,
    x="timestamp",
    y="health_score",
    markers=True,
    title="Equipment Health Over Time"
)

fig_health.update_yaxes(range=[0, 100])

st.plotly_chart(fig_health, use_container_width=True)

# Sensor graphs
col1, col2 = st.columns(2)

with col1:

    st.subheader("🌡 Temperature")

    fig_temp = px.line(
        df,
        x="timestamp",
        y="temperature",
        title="Temperature History"
    )

    st.plotly_chart(
        fig_temp,
        use_container_width=True
    )


with col2:

    st.subheader("📳 Vibration")

    fig_vibration = px.line(
        df,
        x="timestamp",
        y="vibration",
        title="Vibration History"
    )

    st.plotly_chart(
        fig_vibration,
        use_container_width=True
    )

# Current and RPM
col1, col2 = st.columns(2)

with col1:

    st.subheader("⚡ Motor Current")

    fig_current = px.line(
        df,
        x="timestamp",
        y="current",
        title="Motor Current"
    )

    st.plotly_chart(
        fig_current,
        use_container_width=True
    )


with col2:

    st.subheader("🔄 RPM")

    fig_rpm = px.line(
        df,
        x="timestamp",
        y="rpm",
        title="Motor RPM"
    )

    st.plotly_chart(
        fig_rpm,
        use_container_width=True
    )

# Alerts
st.subheader("⚠️ AI Alerts")

if health < 50:
    st.error(
        "Critical equipment condition. "
        "Maintenance should be investigated."
    )

elif health < 70:
    st.warning(
        "Abnormal equipment behavior detected."
    )

else:
    st.success(
        "Equipment operating within the configured health range."
    )
