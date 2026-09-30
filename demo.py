"""
All-In-One MQTT Demonstration Script
This script:
1. Automatically starts the Mosquitto broker in the background if it's not already running.
2. Connects a subscriber to listen for messages.
3. Sends 3 test messages through the broker.
4. Shows how messages travel from Publisher -> Mosquitto -> Subscriber in real time.
"""

import subprocess
import socket
import time
import sys
import paho.mqtt.client as mqtt

BROKER_PATH = r"C:\Program Files\mosquitto\mosquitto.exe"
CONFIG_PATH = "mosquitto.conf"
HOST = "localhost"
PORT = 1883
TOPIC = "home/sensor/temperature"

def is_port_open(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0

def main():
    print("=" * 60)
    print("  MOSQUITTO & PYTHON MQTT - SIMPLE LIVE DEMO")
    print("=" * 60)

    broker_proc = None
    if not is_port_open(HOST, PORT):
        print("\n[Step 1] Mosquitto broker is not running. Starting it now...")
        try:
            broker_proc = subprocess.Popen([BROKER_PATH, "-c", CONFIG_PATH])
            time.sleep(1.5)
            print("         -> Mosquitto broker started successfully!")
        except Exception as e:
            print(f"         -> Error starting Mosquitto: {e}")
            return
    else:
        print("\n[Step 1] Mosquitto broker is already running.")

    messages_received = []

    # Setup Subscriber
    def on_connect(client, userdata, flags, rc, props=None):
        client.subscribe(TOPIC)

    def on_message(client, userdata, msg):
        text = msg.payload.decode("utf-8")
        messages_received.append(text)
        print(f"  [RECEIVER] Got message on '{msg.topic}': {text}")

    print("\n[Step 2] Connecting Subscriber (Receiver) to topic:", TOPIC)
    sub = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    sub.on_connect = on_connect
    sub.on_message = on_message
    sub.connect(HOST, PORT)
    sub.loop_start()
    time.sleep(1)

    # Setup Publisher
    print("\n[Step 3] Connecting Publisher (Sender)...")
    pub = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    pub.connect(HOST, PORT)
    pub.loop_start()
    time.sleep(0.5)

    print("\n[Step 4] Publishing sample messages:")
    test_data = [
        "Temperature reading: 22.5 C",
        "Temperature reading: 23.1 C",
        "Temperature reading: 21.8 C",
    ]

    for item in test_data:
        print(f"  [SENDER]   Publishing: '{item}'")
        pub.publish(TOPIC, item)
        time.sleep(1)

    print("\n" + "-" * 60)
    print(f"Summary: Sent {len(test_data)} messages, Received {len(messages_received)} messages.")
    print("-" * 60)

    # Cleanup
    pub.loop_stop()
    pub.disconnect()
    sub.loop_stop()
    sub.disconnect()

    if broker_proc:
        broker_proc.terminate()
        print("\n[Done] Stopped temporary background broker.")

    print("\nEverything is working! You are ready to build MQTT apps.")

if __name__ == "__main__":
    main()
