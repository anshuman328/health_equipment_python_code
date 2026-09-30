"""
MQTT Subscriber using paho-mqtt (v2.x API)
Listens for incoming messages on topic 'test/topic' and prints them.
"""

import time
import paho.mqtt.client as mqtt

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC = "test/topic"

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[+] Connected to Mosquitto broker at {BROKER_HOST}:{BROKER_PORT}")
        # Subscribe to topic
        client.subscribe(TOPIC)
        print(f"[*] Subscribed to topic: '{TOPIC}'")
    else:
        print(f"[-] Connection failed with reason code: {reason_code}")

def on_message(client, userdata, msg):
    print(f"[Message Received] Topic: '{msg.topic}' | QoS: {msg.qos} | Payload: {msg.payload.decode('utf-8')}")

def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    print(f"[*] Disconnected from broker (Reason code: {reason_code})")

def main():
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    print(f"[*] Connecting to Mosquitto broker at {BROKER_HOST}:{BROKER_PORT}...")
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    except ConnectionRefusedError:
        print("[!] ERROR: Connection refused. Ensure the Mosquitto broker is running!")
        print("    You can start it with: mosquitto -c mosquitto.conf -v")
        return

    print("[*] Waiting for messages (Press Ctrl+C to exit)...")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[*] Stopping subscriber...")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
