#include <Arduino.h>
#include <ArduinoBLE.h>
#include <Arduino_BMI270_BMM150.h>
#include <math.h>

const char* SERVICE_UUID    = "19B10000-E8F2-537E-4F6C-D104768A1214";
const char* CHAR_TELEMETRY  = "19B10001-E8F2-537E-4F6C-D104768A1214";

BLEService sensorService(SERVICE_UUID);
BLECharacteristic telemetryChar(CHAR_TELEMETRY, BLERead | BLENotify, 16);

struct __attribute__((packed)) TelemetryPacket {
    uint32_t timestamp_ms;
    float rms_accel;
    float anomaly_score;
    uint8_t is_anomaly;
    uint8_t reserved[3];
};

void setup() {
    Serial.begin(115200);
    pinMode(LED_BUILTIN, OUTPUT);

    if (!IMU.begin()) {
        Serial.println("Failed to initialize IMU!");
        while (1) {
            digitalWrite(LED_BUILTIN, HIGH);
            delay(100);
            digitalWrite(LED_BUILTIN, LOW);
            delay(100);
        }
    }

    if (!BLE.begin()) {
        Serial.println("Starting BLE failed!");
        while (1);
    }

    BLE.setLocalName("Nano33Sense-Rev2");
    BLE.setAdvertisedService(sensorService);
    sensorService.addCharacteristic(telemetryChar);
    BLE.addService(sensorService);

    BLE.advertise();
    Serial.println("BLE Telemetry Service Started, waiting for Central...");
}

void loop() {
    BLEDevice central = BLE.central();

    if (central) {
        Serial.print("Connected to central: ");
        Serial.println(central.address());
        digitalWrite(LED_BUILTIN, HIGH);

        while (central.connected()) {
            float x, y, z;
            if (IMU.accelerationAvailable()) {
                IMU.readAcceleration(x, y, z);
                float rms = sqrt(x * x + y * y + z * z);

                TelemetryPacket packet;
                packet.timestamp_ms = millis();
                packet.rms_accel = rms;
                packet.anomaly_score = (rms > 1.8f) ? 0.95f : 0.05f;
                packet.is_anomaly = (rms > 1.8f) ? 1 : 0;
                packet.reserved[0] = 0;
                packet.reserved[1] = 0;
                packet.reserved[2] = 0;

                telemetryChar.writeValue((uint8_t*)&packet, sizeof(packet));

                Serial.print("RMS Accel: ");
                Serial.print(rms);
                Serial.print(" G, Anomaly: ");
                Serial.println(packet.is_anomaly ? "YES" : "NO");

                delay(500);
            }
        }

        digitalWrite(LED_BUILTIN, LOW);
        Serial.println("Disconnected from central");
    }
}
