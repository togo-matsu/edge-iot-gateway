import asyncio
import json
import struct
import sys
import paho.mqtt.client as mqtt
from bleak import BleakClient, BleakScanner

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# --- 接続先設定（デバイス名スキャン方式） ---
DEVICE_NAME = "Nano33Sense-Rev2"
SERVICE_UUID = "19B10000-E8F2-537E-4F6C-D104768A1214"
CHAR_UUID = "19B10001-E8F2-537E-4F6C-D104768A1214"

MQTT_BROKER = "10.0.0.201"
MQTT_PORT = 1883
MQTT_TOPIC = "devices/nano33-sense-rev2/telemetry"

OTEL_COLLECTOR_ENDPOINT = "10.0.0.202:4317"

# 1. OpenTelemetry 初期化
resource = Resource.create(attributes={
    "service.name": "edge-ble-gateway",
    "service.namespace": "iot-pipeline",
    "host.name": "raspi5"
})
provider = TracerProvider(resource=resource)
otlp_exporter = OTLPSpanExporter(endpoint=OTEL_COLLECTOR_ENDPOINT, insecure=True)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("edge.ble.gateway", "1.0.0")
propagator = TraceContextTextMapPropagator()

# 2. MQTT 初期化
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT] Connected to EMQX at {MQTT_BROKER}:{MQTT_PORT}", flush=True)
    else:
        print(f"[MQTT] Connection failed with code {rc}", flush=True)

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# 3. BLE 通知コールバック
def notification_handler(sender, data: bytearray):
    if len(data) != 20:
        return

    ts, rms_accel, rms_sound, score, is_anom, anom_type, _ = struct.unpack('<IfffBB2s', data)

    with tracer.start_as_current_span("process_sensor_telemetry") as span:
        span.set_attribute("device.id", "nano33-sense-rev2")
        span.set_attribute("sensor.rms_accel", rms_accel)
        span.set_attribute("sensor.rms_sound", rms_sound)
        span.set_attribute("anomaly.score", score)
        span.set_attribute("anomaly.detected", bool(is_anom))
        span.set_attribute("anomaly.type", anom_type)

        carrier = {}
        propagator.inject(carrier=carrier)

        payload = {
            "timestamp_ms": ts,
            "rms_accel": round(rms_accel, 4),
            "rms_sound": round(rms_sound, 2),
            "anomaly_score": round(score, 4),
            "is_anomaly": bool(is_anom),
            "anomaly_type": anom_type,
            "traceparent": carrier.get("traceparent")
        }

        mqtt_client.publish(MQTT_TOPIC, json.dumps(payload))

        anom_labels = ["NORMAL", "ACCEL_ANOMALY", "SOUND_ANOMALY", "COMPLEX_ANOMALY"]
        label = anom_labels[anom_type] if anom_type < len(anom_labels) else "UNKNOWN"
        print(f"[Edge] Sent -> Accel: {rms_accel:.2f}G | Sound: {rms_sound:.1f} | Type: {label} (Score: {score:.2f})", flush=True)

# 4. BLE 名前スキャン & 接続ループ
async def main():
    print(f"[BLE] Scanning for '{DEVICE_NAME}'...", flush=True)
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)

    if not device:
        print(f"[BLE] Error: Device '{DEVICE_NAME}' not found.", flush=True)
        return

    print(f"[BLE] Found '{device.name}' ({device.address})! Connecting...", flush=True)
    async with BleakClient(device, timeout=15.0) as client:
        print(f"[BLE] Connected! Subscribing to telemetry notifications...", flush=True)
        await client.start_notify(CHAR_UUID, notification_handler)

        print("[BLE] Streaming sensor data to MQTT pipeline. Press Ctrl+C to stop.", flush=True)
        while True:
            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Edge] Stopping gateway...", flush=True)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        provider.shutdown()
        sys.exit(0)
