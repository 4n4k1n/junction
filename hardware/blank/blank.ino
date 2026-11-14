// Empty sketch for the Arduino UNO Q: replaces whatever was running and leaves the board idle.
// The LED matrix is cleared and the red built-in LED is switched off (it is active-low).

#include <Arduino_LED_Matrix.h>

Arduino_LED_Matrix matrix;

void setup() {
  matrix.begin();
  matrix.clear();
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);
}

void loop() {
}
