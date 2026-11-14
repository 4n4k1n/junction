// LearnLens lesson "ir-hand-detector" – reference sketch for the Arduino UNO Q (MCU side).
//
// Wiring (USB unplugged while wiring):
//   KY-032 +V  -> 3.3V   (UNO Q GPIO is 3.3V logic)
//   KY-032 GND -> GND
//   KY-032 OUT -> D2
//   KY-032 EN  -> not connected (keep the green EN jumper on)
//
// The KY-032 output is ACTIVE LOW: HIGH = nothing there, LOW = obstacle/hand detected.

const int SENSOR_PIN = 2;
const int OUTPUT_LED = LED_BUILTIN;  // if your core reports this as undefined, use one of the
                                     // UNO Q user LED pin names from the board's pinout instead

void setup() {
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  pinMode(OUTPUT_LED, OUTPUT);
}

void loop() {
  bool handDetected = digitalRead(SENSOR_PIN) == LOW;
  // Some onboard LEDs are wired active-low. If the LED is ON without a hand, swap HIGH and LOW.
  digitalWrite(OUTPUT_LED, handDetected ? HIGH : LOW);
  delay(20);
}
