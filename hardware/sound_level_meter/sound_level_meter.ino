// Sound level meter for the Arduino UNO Q: the louder it gets, the more of the board's
// 8x13 LED matrix lights up, column by column from left to right.
//
// Wiring (USB unplugged while wiring; keep the existing three wires and add one):
//   KY-037 G  -> GND
//   KY-037 +  -> +3V3   (must stay on 3.3 V: A0 is not 5 V-tolerant, about 3.6 V absolute max)
//   KY-037 AO -> A0     (new)
//   KY-037 DO -> D2     (not used by this sketch; may stay connected)
//
// The microphone signal on AO swings around a resting voltage. Loudness is how far it swings
// within a short window (highest reading minus lowest reading). The meter calibrates itself:
// it tracks the quietest and the loudest swing it has seen, so no fixed threshold is needed
// and the blue trim pot does not matter here.

#include <Arduino_LED_Matrix.h>

const int MIC_PIN = A0;
const int ROWS = 8;
const int COLUMNS = 13;
const unsigned long WINDOW_MS = 25;  // one loudness measurement
const int MIN_RANGE = 12;            // smallest loud-quiet span, so silence does not flicker

Arduino_LED_Matrix matrix;
uint8_t frame[ROWS * COLUMNS];

float quiet = 1e9;    // smallest swing seen: the room's noise floor
float loud = 0;       // largest swing seen recently
float shown = 0;      // bar length on the matrix, in columns

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);  // brightness values 0..7
  matrix.clear();
}

void loop() {
  // Measure the swing of the microphone signal over one window.
  int lowest = 100000;
  int highest = 0;
  unsigned long start = millis();
  while (millis() - start < WINDOW_MS) {
    int value = analogRead(MIC_PIN);
    if (value < lowest) lowest = value;
    if (value > highest) highest = value;
  }
  float swing = highest - lowest;

  // Self-calibration: the floor creeps up slowly, the ceiling creeps down slowly.
  if (swing < quiet) quiet = swing; else quiet += 0.002f * (swing - quiet);
  if (swing > loud) loud = swing; else loud -= 0.01f * (loud - quiet);
  float range = loud - quiet;
  if (range < MIN_RANGE) range = MIN_RANGE;

  // 0 = silence, COLUMNS = as loud as it has been. Rise at once, fall gently.
  float level = (swing - quiet) / range * COLUMNS;
  if (level < 0) level = 0;
  if (level > COLUMNS) level = COLUMNS;
  if (level > shown) shown = level; else shown -= 0.6f;
  if (shown < 0) shown = 0;

  // Light whole columns up to the level; the last, partly reached column is dimmer.
  int full = (int)shown;
  int partial = (int)((shown - full) * 7);
  for (int row = 0; row < ROWS; row++) {
    for (int column = 0; column < COLUMNS; column++) {
      uint8_t brightness = 0;
      if (column < full) brightness = 7;
      else if (column == full) brightness = partial;
      frame[row * COLUMNS + column] = brightness;
    }
  }
  matrix.draw(frame);
}
