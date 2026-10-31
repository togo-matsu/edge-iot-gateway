#include <Arduino.h>
#include <ArduinoBLE.h>
#include <Arduino_BMI270_BMM150.h>
#include <PDM.h>
#include <math.h>
#include "anomaly_model.h"

const char* DEVICE_NAME     = "Nano33Sense-Rev2";
const char* SERVICE_UUID    = "19B10000-E8F2-537E-4F6C-D104768A1214";
const char* CHAR_TELEMETRY  = "19B10001-E8F2-537E-4F6C-D104768A1214";

BLEService sensorService(SERVICE_UUID);
BLECharacteristic telemetryChar(CHAR_TELEMETRY, BLERead | BLENotify, 20);

struct __attribute__((packed)) TelemetryPacket {
    uint32_t timestamp_ms;
    float    rms_accel;
    float    rms_sound;
    float    anomaly_score;
    uint8_t  is_anomaly;
    uint8_t  anomaly_type;
    uint8_t  reserved[2];
};

// PDM デフォルトバッファ(512バイト = short 256個)に合わせたサイズ定義
#define SOUND_BUFFER_SIZE 256
short soundBuffer[SOUND_BUFFER_SIZE];
volatile int samplesRead = 0;

void onPDMdata() {
    int bytesAvailable = PDM.available();
    if (bytesAvailable > (int)sizeof(soundBuffer)) {
        bytesAvailable = sizeof(soundBuffer);
    }
    if (bytesAvailable > 0) {
        PDM.read(soundBuffer, bytesAvailable);
        samplesRead = bytesAvailable / sizeof(short);
    }
}

float calculateSoundRMS() {
    int count = samplesRead;
    if (count <= 0) return 0.0f;
    samplesRead = 0;

    long long sumSquares = 0;
    for (int i = 0; i < count; i++) {
        sumSquares += (long long)soundBuffer[i] * soundBuffer[i];
    }
    return sqrt((float)(sumSquares / count));
}

void setup() {
    Serial.begin(115200);
    pinMode(LED_BUILTIN, OUTPUT);

    for (int i = 0; i < 3; i++) {
        digitalWrite(LED_BUILTIN, HIGH); delay(150);
        digitalWrite(LED_BUILTIN, LOW);  delay(150);
    }

    // 1. BLE 初期化（最優先）
    if (!BLE.begin()) {
        while (1) {
            digitalWrite(LED_BUILTIN, HIGH); delay(800);
            digitalWrite(LED_BUILTIN, LOW);  delay(800);
        }
    }

    // デバイス名とアドバタイズ名の両方を設定
    BLE.setDeviceName(DEVICE_NAME);
    BLE.setLocalName(DEVICE_NAME);
    BLE.setAdvertisedService(sensorService);
    sensorService.addCharacteristic(telemetryChar);
    BLE.addService(sensorService);
    BLE.advertise();

    // 2. IMU 初期化
    if (!IMU.begin()) {
        while (1) {
            digitalWrite(LED_BUILTIN, HIGH); delay(100);
            digitalWrite(LED_BUILTIN, LOW);  delay(100);
        }
    }

    // 3. PDM マイク初期化 (512バイトバッファを明示指定)
    PDM.setBufferSize(sizeof(soundBuffer));
    PDM.onReceive(onPDMdata);
    if (!PDM.begin(1, 16000)) {
        while (1) {
            digitalWrite(LED_BUILTIN, HIGH); delay(300);
            digitalWrite(LED_BUILTIN, LOW);  delay(300);
        }
    }
    PDM.setGain(20);
}

void loop() {
    BLEDevice central = BLE.central();

    if (central) {
        digitalWrite(LED_BUILTIN, HIGH);
        Serial.print("[BLE] Connected to central: ");
        Serial.println(central.address());

        unsigned long lastSend = 0;
        while (central.connected()) {
            BLE.poll();
            unsigned long now = millis();
            if (now - lastSend >= 500) {
                lastSend = now;
                float x, y, z;
                if (IMU.accelerationAvailable()) {
                    IMU.readAcceleration(x, y, z);
                    float rms_accel = sqrt(x * x + y * y + z * z);
                    float rms_sound = calculateSoundRMS();

                    float dist_sq = compute_mahalanobis_sq(rms_accel, rms_sound);
                    float score = dist_sq / (THRESHOLD_ANOMALY * 2.0f);
                    if (score > 1.0f) score = 1.0f;
                    if (score < 0.0f) score = 0.0f;

                    bool is_anom = (dist_sq > THRESHOLD_ANOMALY);
                    uint8_t anom_type = 0;

                    if (is_anom) {
                        bool accel_deviated = (fabs(rms_accel - MODEL_MU_ACCEL) > 0.25f);
                        bool sound_deviated = ((rms_sound - MODEL_MU_SOUND) > 30.0f);

                        if (accel_deviated && sound_deviated) anom_type = 3;
                        else if (accel_deviated) anom_type = 1;
                        else if (sound_deviated) anom_type = 2;
                        else anom_type = 1;
                    }

                    TelemetryPacket packet;
                    packet.timestamp_ms  = now;
                    packet.rms_accel     = rms_accel;
                    packet.rms_sound     = rms_sound;
                    packet.anomaly_score = score;
                    packet.is_anomaly    = is_anom ? 1 : 0;
                    packet.anomaly_type  = anom_type;
                    packet.reserved[0]   = 0;
                    packet.reserved[1]   = 0;

                    telemetryChar.writeValue((uint8_t*)&packet, sizeof(packet));
                }
            }
        }

        digitalWrite(LED_BUILTIN, LOW);
        BLE.advertise();
    } else {
        static unsigned long lastLog = 0;
        if (millis() - lastLog >= 2000) {
            lastLog = millis();
            Serial.print("[WAITING] BLE Advertising Active | Name: ");
            Serial.print(DEVICE_NAME);
            Serial.print(" | MAC: ");
            Serial.println(BLE.address());
        }
    }
}
