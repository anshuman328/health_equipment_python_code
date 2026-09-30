# Mosquitto & Python MQTT Setup Guide

This workspace contains everything you need to run the **Mosquitto MQTT Broker** and interact with it using **Python** (`paho-mqtt`).

---

## 1. What Has Been Configured

1. **Mosquitto Broker**: Located at `C:\Program Files\mosquitto`. Added to your user `PATH` environment variable so you can run `mosquitto`, `mosquitto_sub`, and `mosquitto_pub` directly from any terminal.
2. **Python Library**: `paho-mqtt` (version 2.1.0) is installed.
3. **Configuration**: `mosquitto.conf` is configured for local development on port `1883` with `allow_anonymous true` (Mosquitto 2.x requires explicit configuration to accept anonymous connections).

---

## 2. Quick Start

### Step 1: Start the Mosquitto Broker
Open a terminal in this directory (`C:\Users\anshu\.gemini\antigravity\scratch\mosquitto-python-setup`) and run:

```powershell
mosquitto -c mosquitto.conf -v
```

> **Note**: Leave this terminal open. The `-v` flag provides verbose logging so you can see connections, subscriptions, and published messages in real time.

*(Alternatively, to run Mosquitto in the background as a Windows service, open PowerShell as Administrator and run `Start-Service mosquitto`).*

---

### Step 2: Run the Subscriber
Open a second terminal window and run:

```powershell
python subscriber.py
```

You should see:
```text
[*] Connecting to Mosquitto broker at localhost:1883...
[+] Connected to Mosquitto broker at localhost:1883
[*] Subscribed to topic: 'test/topic'
[*] Waiting for messages (Press Ctrl+C to exit)...
```

---

### Step 3: Run the Publisher
Open a third terminal window and run:

```powershell
python publisher.py
```

The publisher will send JSON-formatted sensor data to `test/topic` every 3 seconds, which will immediately appear in the subscriber terminal!

---

## 3. Testing with Command-Line Tools

Mosquitto includes built-in CLI tools:

- **Subscribe via CLI**:
  ```powershell
  mosquitto_sub -t "test/topic" -v
  ```

- **Publish a message via CLI**:
  ```powershell
  mosquitto_pub -t "test/topic" -m "Hello from Mosquitto CLI!"
  ```

---

## 4. Enabling Password Authentication (Optional)

For production or secured setups:

1. Create a password file using `mosquitto_passwd`:
   ```powershell
   mosquitto_passwd -c passwordfile.txt your_username
   ```
   *(It will prompt you to enter a password).*

2. Update `mosquitto.conf`:
   ```conf
   listener 1883 0.0.0.0
   allow_anonymous false
   password_file passwordfile.txt
   ```

3. Update your Python scripts to authenticate before connecting:
   ```python
   client.username_pw_set("your_username", "your_password")
   ```
