import asyncio
import struct
import json
from bleak import BleakScanner, BleakClient
import paho.mqtt.client as mqtt

# OpenTelemetry のインポート
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# --- 1. OpenTelemetry トレーサーの初期化 ---

# Tempo / Grafana 上で表示されるサービス名とリソース属性を定義
resource = Resource.create(attributes={
    "service.name": "edge-ble-gateway",
    "host.name": "raspberrypi-5",
    "device.id": "nano33-sense-rev2"
})

provider = TracerProvider(resource=resource)

# K8s 内の OpenTelemetry Collector VIP (10.0.0.202:4317) への gRPC エクスポーター
otlp_exporter = OTLPSpanExporter(
    endpoint="10.0.0.202:4317",
    insecure=True  # 内部通信（プレーンテキスト gRPC）
)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

# ローカル確認用（ターミナルにもスパンを出したい場合は下のコメントアウトを解除）
# provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

trace.set_tracer_provider(provider)
tracer = trace.get_tracer("edge.ble.gateway", "1.0.0")
propagator = TraceContextTextMapPropagator()

# --- 2. 接続設定 ---
DEVICE_NAME = "Nano33Sense-Rev2"
CHAR_UUID   = "19B10001-E8F2-537E-4F6C-D104768A1214"
MQTT_BROKER = "10.0.0.201"
MQTT_PORT   = 1883
TOPIC_TELEMETRY = "devices/nano33-sense-rev2/telemetry"
TOPIC_ALERT     = "factory/alerts/nano33-sense"

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="raspi5-ble-gateway")

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT] Connected to EMQX at {MQTT_BROKER}:{MQTT_PORT}")
    else:
        print(f"[MQTT] Connection failed with code {rc}")

mqtt_client.on_connect = on_connect
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# --- 3. BLE 通知受信 & OTel スパン生成処理 ---
def handle_notification(sender, data: bytearray):
    if len(data) == 16:
        uptime_ms, rms_accel, anomaly_score, is_anomaly, _, _, _ = struct.unpack("<IffBBBB", data)
        
        # 新しいスパンを開始（エッジ処理の計測・Trace ID の採番）
        with tracer.start_as_current_span("process_sensor_telemetry") as span:
            span_context = span.get_span_context()
            trace_id = format(span_context.trace_id, "032x")
            span_id = format(span_context.span_id, "016x")
            
            # W3C traceparent 文字列を生成 (00-<trace_id>-<span_id>-01)
            traceparent = f"00-{trace_id}-{span_id}-01"
            
            # スパンにセンサー属性をメタデータとして付与
            span.set_attribute("device.id", "nano33-sense-rev2")
            span.set_attribute("sensor.rms_accel", round(rms_accel, 3))
            span.set_attribute("sensor.anomaly_score", round(anomaly_score, 2))
            span.set_attribute("sensor.is_anomaly", bool(is_anomaly))
            
            # MQTT 送信用 JSON ペイロード（traceparent を注入）
            payload = {
                "traceparent": traceparent,
                "device_id": "nano33-sense-rev2",
                "uptime_ms": uptime_ms,
                "rms_accel": round(rms_accel, 3),
                "anomaly_score": round(anomaly_score, 2),
                "is_anomaly": bool(is_anomaly)
            }
            json_data = json.dumps(payload)
            
            # 通常テレメトリの Publish
            mqtt_client.publish(TOPIC_TELEMETRY, json_data)
            print(f"[BLE->MQTT] TraceID: {trace_id[:8]}... | Telemetry: {json_data}")
            
            # 異常検知時のアラート Publish
            if is_anomaly:
                span.set_attribute("event.alert", True)
                mqtt_client.publish(TOPIC_ALERT, json_data)
                print(f"[ALERT!] Anomaly Detected (TraceID: {trace_id[:8]}...)")

# --- 4. BLE 接続ループ ---
async def main():
    while True:
        print(f"[BLE] Scanning for {DEVICE_NAME}...")
        device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
        
        if not device:
            print(f"[BLE] {DEVICE_NAME} not found. Retrying in 3 seconds...")
            await asyncio.sleep(3)
            continue
            
        print(f"[BLE] Found device: {device.address}. Connecting...")
        try:
            async with BleakClient(device) as client:
                print(f"[BLE] Connected successfully!")
                await client.start_notify(CHAR_UUID, handle_notification)
                while client.is_connected:
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"[BLE] Error: {e}")
        
        print("[BLE] Disconnected. Reconnecting...")
        await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping Gateway...")
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
