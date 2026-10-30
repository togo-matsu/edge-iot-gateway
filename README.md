# Edge IoT Gateway (Raspberry Pi 5)

Arduino Nano 33 BLE Sense からの BLE テレメトリを受信し、OpenTelemetry 分散トレースコンテキスト（W3C TraceContext）を注入して EMQX MQTT Broker へ転送するエッジゲートウェイ。

## 機能
- BLE Central 通信 (bleak)
- MQTT Publish (paho-mqtt)
- 分散トレーシング (OpenTelemetry gRPC Exporter -> K8s OTel Collector VIP: 10.0.0.202:4317)

## 起動方法
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python ble_to_mqtt.py
