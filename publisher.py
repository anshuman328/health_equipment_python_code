"""
MQTT Publisher using paho-mqtt (v2.x API)
Publishes messages to topic 'test/topic'.
"""

import json
import time
from datetime import datetime
import paho.mqtt.client as mqtt

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC = "test/topic"

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[+] Connected to Mosquitto broker at {BROKER_HOST}:{BROKER_PORT}")
    else:
        print(f"[-] Connection failed with reason code: {reason_code}")

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    print(f"[+] Message ID {mid} successfully published.")

def main():
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_publish = on_publish

    print(f"[*] Connecting to Mosquitto broker at {BROKER_HOST}:{BROKER_PORT}...")
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    except ConnectionRefusedError:
        print("[!] ERROR: Connection refused. Ensure the Mosquitto broker is running!")
        print("    You can start it with: mosquitto -c mosquitto.conf -v")
        return

    # Start network loop in a background thread
    client.loop_start()

    time.sleep(1)  # Allow brief moment to establish connection

    print("[*] Starting message publishing loop (Press Ctrl+C to stop)...")
    counter = 1
    try:
        while True:
            payload = {
                "equipment_id": "MOTOR-01",
                "timestamp": datetime.now().isoformat(),
                "temperature": round(62 + (counter % 8) * 1.2, 2),
                "vibration": round(2 + (counter % 6) * 0.25, 2),
                "current": round(12 + (counter % 5) * 0.4, 2),
                "rpm": 1450 + (counter % 7) * 4,
                "pressure": round(3.1 + (counter % 5) * 0.08, 2),
            }
            payload_str = json.dumps(payload)
            print(f"\n[Publishing] {payload_str}")
            client.publish(TOPIC, payload=payload_str, qos=1)
            counter += 1
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[*] Stopping publisher...")
    finally:
        client.loop_stop()
        client.disconnect()
        print("[*] Disconnected.")

if __name__ == "__main__":
    main()
