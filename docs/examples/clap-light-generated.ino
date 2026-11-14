// LearnLens: Clap-activated light
// Board: Arduino UNO Q (FQBN arduino:zephyr:unoq). Not a classic Arduino UNO.
// Wiring: sensor DO -> D2, + -> +3V3, G -> GND.
#include <Arduino_RouterBridge.h>

const int SENSOR_PIN = 2;  // header D2, input only
const int LED_PIN = LED_BUILTIN;
const int LED_ON_LEVEL = LOW;  // this board's LED lights at LOW
const int LED_OFF_LEVEL = HIGH;
const int SENSOR_ACTIVE_LEVEL = HIGH;  // level on SENSOR_PIN while the sensor reports sound
const unsigned long DEBOUNCE_MS = 300;  // after an event, ignore new events for this long

bool ledOn = false;
bool wasActive = false;
unsigned long lastEventMs = 0;

void setup() {
  pinMode(SENSOR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LED_OFF_LEVEL);
  Monitor.begin();
  Monitor.println("READY");
}

void loop() {
  bool active = digitalRead(SENSOR_PIN) == SENSOR_ACTIVE_LEVEL;
  unsigned long now = millis();
  // Edge detection: act only when the pin has just become active, and not too soon after the last event.
  if (active && !wasActive && now - lastEventMs >= DEBOUNCE_MS) {
    lastEventMs = now;
    ledOn = !ledOn;
    Monitor.println("SOUND_DETECTED");
    digitalWrite(LED_PIN, ledOn ? LED_ON_LEVEL : LED_OFF_LEVEL);
    Monitor.println(ledOn ? "LED_ON" : "LED_OFF");
  }
  wasActive = active;
}
