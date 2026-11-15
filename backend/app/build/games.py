"""Projects that start from a goal instead of a photo: Pong and Snake on the board's LED matrix,
played with the KY-023 joystick.

The text here says what a game should do; pins and voltages still come from the planner and the
hardware database. Each game also has a prevalidated sketch, used without the AI service and when
no AI attempt passes the checks.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

from .models import CodegenContext, GeneratedSketch, InputPin, SerialEvent

DEFAULT_OFFER = "pong,snake,level_meter"  # LEARNLENS_OFFERED_PROJECTS: the games here and the fixed projects
JOYSTICK = "ky-023"
RESET_SENSOR = "ky-032"

COMMON = ("The joystick's resting position is not known in advance: measure both axes once in setup() and treat "
          "a clear deflection from that as input. Draw on the whole LED matrix. Log NEW_GAME each time a game starts.")

GAMES = {
    "pong": {
        "title": "Pong",
        "summary": "Keep the ball in play with a paddle you move with the joystick.",
        "words": ("pong", "ping pong", "ping-pong", "tennis", "paddle"),
        "behavior": "A one-player Pong game. A paddle three pixels tall sits in the left column and moves up and "
                    "down with the joystick. A ball bounces off the top, bottom and right edges and off the paddle. "
                    "Each hit makes the ball a little faster and logs HIT. When the ball passes the paddle, log MISS, "
                    "flash the matrix briefly and start a new game. " + COMMON,
        "events": [("HIT", "each time the paddle returns the ball"), ("MISS", "each time the ball passes the paddle")],
    },
    "snake": {
        "title": "Snake",
        "summary": "Steer a growing snake to the food with the joystick.",
        "words": ("snake", "schlange", "snek"),
        "behavior": "A Snake game. The snake moves one pixel at a time and is steered in four directions with the "
                    "joystick; it cannot turn back onto itself. It leaves the matrix on one side and comes back in on "
                    "the other. Eating the food pixel makes it one longer, logs FOOD and places new food. When the "
                    "head hits the body, log GAME_OVER, flash the matrix briefly and start a new game. " + COMMON,
        "events": [("FOOD", "each time the snake eats"), ("GAME_OVER", "each time the snake runs into itself")],
    },
}

RESET_IDEA = "Hold a hand over it to restart the game"
RESET_BEHAVIOR = " When the infrared sensor newly detects a hand in front of it, start a new game at once."
RESET_WORDS = ("reset", "restart", "start again", "start over", "new game", "neu start", "neustart", "neu starten",
               "zurücksetz", "zuruecksetz", "von vorn")


def offered() -> List[str]:
    names = {n.strip() for n in os.getenv("LEARNLENS_OFFERED_PROJECTS", DEFAULT_OFFER).split(",")}
    return [g for g in GAMES if g in names]


def game_for(text: str, current: str = "") -> Optional[str]:
    """The offered game a request names, if any. "Snake instead of Pong" names both: for a build
    that is `current`ly one of them, the other one is meant."""
    text = text.lower()
    named = [g for g in offered() if any(re.search(rf"\b{re.escape(w)}\b", text) for w in GAMES[g]["words"])]
    return next((g for g in named if g != current), named[0] if named else None)


def wants_reset(text: str) -> bool:
    return any(w in text.lower() for w in RESET_WORDS)


def behavior(game: str, reset: bool = False) -> str:
    return GAMES[game]["behavior"] + (RESET_BEHAVIOR if reset else "")


def offer() -> str:
    """What to tell a user whose wish is not one of the offered projects."""
    names = {n.strip() for n in os.getenv("LEARNLENS_OFFERED_PROJECTS", DEFAULT_OFFER).split(",")}
    text = f"Right now I can build {' or '.join(GAMES[g]['title'] for g in offered())} with the joystick"
    if "level_meter" in names:
        text += ", or a sound level meter with the microphone sensor"
    return text + ". Ask me for one of them."


# ---------- prevalidated sketches ----------

def _inputs(ctx: CodegenContext):
    """(x axis, y axis, reset sensor or None) from the confirmed wiring, or RuntimeError."""
    analog = [i for i in ctx.inputs if i.kind == "analog"]
    digital = [i for i in ctx.inputs if i.kind == "digital"]
    if len(analog) != 2 or len(digital) > 1 or any(i.component != JOYSTICK for i in analog):
        raise RuntimeError("This change needs the AI service: there is no prevalidated sketch for it.")
    return analog[0], analog[1], (digital[0] if digital else None)


def _head(ctx: CodegenContext, x: InputPin, y: InputPin, reset: Optional[InputPin]) -> str:
    wiring = f"joystick VRx -> {x.header}, VRy -> {y.header}, +5V pin -> +3V3, GND -> GND"
    if reset:
        wiring += f"; {reset.name.split()[0]} OUT -> {reset.header}"
    code = f"""\
// LearnLens: {GAMES[ctx.project.game]["title"]}
// Board: {ctx.board_name} (FQBN {ctx.fqbn}). Not a classic Arduino UNO.
// Wiring: {wiring}. Everything runs on 3.3 V.
#include <{ctx.allowed_includes[0]}>
#include <{ctx.allowed_includes[1]}>

const int X_PIN = {x.header};
const int Y_PIN = {y.header};
"""
    if reset:
        code += (f"const int RESET_PIN = {reset.number};  // header {reset.header}: {reset.name}\n"
                 f"const int RESET_ACTIVE_LEVEL = {reset.active_level};  // level while the sensor detects something\n")
    return code + f"""\
const int ROWS = {ctx.matrix_rows};
const int COLUMNS = {ctx.matrix_columns};
const int BRIGHTEST = {ctx.matrix_max_brightness};
const unsigned long FLASH_MS = 400;  // how long the matrix lights up after a lost game

{ctx.matrix_type} matrix;
uint8_t frame[{ctx.matrix_rows * ctx.matrix_columns}];
int centerX = 0;  // joystick at rest, measured in setup()
int centerY = 0;
unsigned long flashUntil = 0;
"""


def _setup(ctx: CodegenContext, reset: Optional[InputPin]) -> str:
    return f"""\
void setup() {{
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
{"  pinMode(RESET_PIN, INPUT);" + chr(10) if reset else ""}\
  {" ".join(ctx.log_setup)}
  // Where the joystick rests: the average of 16 readings. Do not touch it while the board starts.
  long sumX = 0;
  long sumY = 0;
  for (int i = 0; i < 16; i++) {{
    sumX += analogRead(X_PIN);
    sumY += analogRead(Y_PIN);
  }}
  centerX = sumX / 16;
  centerY = sumY / 16;
  newGame();
  {ctx.log_object}.println("READY");
}}
"""


RESET_STATE = "bool handWasThere = false;  // to notice the moment a hand appears\n"
RESET_LOOP = """\
  // A hand that newly appears in front of the second sensor starts a new game.
  bool handThere = digitalRead(RESET_PIN) == RESET_ACTIVE_LEVEL;
  if (handThere && !handWasThere) newGame();
  handWasThere = handThere;

"""
DRAW_FLASH = """\
  for (int i = 0; i < ROWS * COLUMNS; i++) frame[i] = now < flashUntil ? 2 : 0;
"""


def _pong(ctx: CodegenContext, x: InputPin, y: InputPin, reset: Optional[InputPin]) -> str:
    return _head(ctx, x, y, reset) + f"""\
const int PADDLE = 3;                       // paddle height in pixels
const unsigned long PADDLE_STEP_MS = 70;    // paddle speed
const unsigned long BALL_STEP_MS = 170;     // ball speed at the start of a game
const unsigned long FASTEST_STEP_MS = 60;

int paddle = 0;  // top row of the paddle
int ballX = 0;
int ballY = 0;
int dirX = 1;
int dirY = 1;
unsigned long ballStep = BALL_STEP_MS;
unsigned long lastBall = 0;
unsigned long lastPaddle = 0;
{RESET_STATE if reset else ""}
void newGame() {{
  paddle = (ROWS - PADDLE) / 2;
  ballX = COLUMNS / 2;
  ballY = ROWS / 2;
  dirX = 1;
  dirY = 1;
  ballStep = BALL_STEP_MS;
  {ctx.log_object}.println("NEW_GAME");
}}

{_setup(ctx, reset)}
void loop() {{
  unsigned long now = millis();
{RESET_LOOP if reset else ""}\
  // The paddle follows whichever joystick axis is pushed further.
  int dx = analogRead(X_PIN) - centerX;
  int dy = analogRead(Y_PIN) - centerY;
  int push = abs(dx) > abs(dy) ? dx : dy;
  int threshold = (centerX + centerY) / 6;  // a third of the way from rest to the end stop
  if (now - lastPaddle >= PADDLE_STEP_MS) {{
    lastPaddle = now;
    if (push > threshold && paddle < ROWS - PADDLE) paddle++;
    if (push < -threshold && paddle > 0) paddle--;
  }}

  if (now - lastBall >= ballStep) {{
    lastBall = now;
    if (ballY + dirY < 0 || ballY + dirY >= ROWS) dirY = -dirY;  // top and bottom edge
    if (ballX + dirX >= COLUMNS) dirX = -1;                      // right edge
    int nextX = ballX + dirX;
    int nextY = ballY + dirY;
    if (nextX == 0) {{  // the paddle's column
      if (nextY >= paddle && nextY < paddle + PADDLE) {{
        dirX = 1;
        nextX = ballX + dirX;
        if (ballStep > FASTEST_STEP_MS) ballStep -= 10;
        {ctx.log_object}.println("HIT");
      }} else {{
        {ctx.log_object}.println("MISS");
        flashUntil = now + FLASH_MS;
        newGame();
        return;
      }}
    }}
    ballX = nextX;
    ballY = nextY;
  }}

{DRAW_FLASH}\
  if (now >= flashUntil) {{
    for (int row = paddle; row < paddle + PADDLE; row++) frame[row * COLUMNS] = BRIGHTEST;
    frame[ballY * COLUMNS + ballX] = BRIGHTEST;
  }}
  matrix.draw(frame);
}}
"""


def _snake(ctx: CodegenContext, x: InputPin, y: InputPin, reset: Optional[InputPin]) -> str:
    cells = ctx.matrix_rows * ctx.matrix_columns
    return _head(ctx, x, y, reset) + f"""\
const int CELLS = {cells};
const unsigned long STEP_MS = 220;  // snake speed

uint8_t body[{cells}];  // cells of the snake, head first; a cell is row * COLUMNS + column
int length = 3;
int dirX = 1;
int dirY = 0;
int food = 0;
unsigned long lastStep = 0;
unsigned long seed = 1;
{RESET_STATE if reset else ""}
bool onSnake(int cell) {{
  for (int i = 0; i < length; i++) if (body[i] == cell) return true;
  return false;
}}

void placeFood() {{
  for (int tries = 0; tries < 200; tries++) {{
    seed = seed * 1103515245UL + 12345UL;
    food = (seed >> 8) % CELLS;
    if (!onSnake(food)) return;
  }}
}}

void newGame() {{
  length = 3;
  dirX = 1;
  dirY = 0;
  for (int i = 0; i < length; i++) body[i] = (ROWS / 2) * COLUMNS + (4 - i);
  seed += millis();
  placeFood();
  {ctx.log_object}.println("NEW_GAME");
}}

{_setup(ctx, reset)}
void loop() {{
  unsigned long now = millis();
{RESET_LOOP if reset else ""}\
  // Steer with the axis that is pushed further. The snake cannot turn back onto itself.
  int dx = analogRead(X_PIN) - centerX;
  int dy = analogRead(Y_PIN) - centerY;
  int threshold = (centerX + centerY) / 6;  // a third of the way from rest to the end stop
  if (abs(dx) >= abs(dy) && abs(dx) > threshold && dirX == 0) {{
    dirX = dx > 0 ? 1 : -1;
    dirY = 0;
  }} else if (abs(dy) > abs(dx) && abs(dy) > threshold && dirY == 0) {{
    dirY = dy > 0 ? 1 : -1;
    dirX = 0;
  }}

  if (now - lastStep >= STEP_MS && now >= flashUntil) {{
    lastStep = now;
    int column = (body[0] % COLUMNS + dirX + COLUMNS) % COLUMNS;  // leaves on one side, returns on the other
    int row = (body[0] / COLUMNS + dirY + ROWS) % ROWS;
    int head = row * COLUMNS + column;
    if (onSnake(head)) {{
      {ctx.log_object}.println("GAME_OVER");
      flashUntil = now + FLASH_MS;
      newGame();
      return;
    }}
    bool ate = head == food;
    if (ate && length < CELLS) length++;
    for (int i = length - 1; i > 0; i--) body[i] = body[i - 1];
    body[0] = head;
    if (ate) {{
      {ctx.log_object}.println("FOOD");
      placeFood();
    }}
  }}

{DRAW_FLASH}\
  if (now >= flashUntil) {{
    for (int i = 0; i < length; i++) frame[body[i]] = i == 0 ? BRIGHTEST : 3;
    frame[food] = (now / 150) % 2 ? BRIGHTEST : 1;  // the food blinks
  }}
  matrix.draw(frame);
}}
"""


def template(ctx: CodegenContext) -> GeneratedSketch:
    """The prevalidated sketch for the project's game, with a restart by the second sensor if one is wired."""
    game = ctx.project.game
    if game not in GAMES:
        raise RuntimeError("This change needs the AI service: there is no prevalidated sketch for it.")
    x, y, reset = _inputs(ctx)
    code = (_pong if game == "pong" else _snake)(ctx, x, y, reset)
    how = {"pong": "Move the joystick to slide the paddle and keep the ball in play. Every hit makes it faster.",
           "snake": "Steer the snake to the blinking food with the joystick. Don't run into yourself."}[game]
    told = {"pong": "It moves a three-pixel paddle in the left column and bounces a ball off the edges and the "
                    "paddle, a little faster after each hit.",
            "snake": "It moves the snake one pixel at a time, lets it grow when it reaches the food and ends the "
                     "game when the head meets the body."}[game]
    return GeneratedSketch(
        source_code=code,
        display_summary=how + (" Hold a hand over the infrared sensor to restart." if reset else ""),
        explanation=(f"The sketch measures where the joystick rests when the board starts, then reads both axes on "
                     f"{x.header} and {y.header} many times a second. {told} The picture is drawn on the board's LED "
                     "matrix." + (f" It also reads the {reset.name} on {reset.header}: a hand that newly appears "
                                  "there starts a new game." if reset else "")),
        assumptions=[
            f"The joystick's VRx and VRy pins are wired to {x.header} and {y.header} and it is powered from +3V3, never from 5 V.",
            "Do not touch the joystick while the board starts: its resting position is measured then.",
            "Which way the joystick has to be held for up and down is not known: if a direction is reversed, turn the joystick around.",
            "The matrix shows the board's boot animation for about half a minute after power-up before the game appears.",
        ] + ([f"The {reset.name} reads {reset.active_level} on {reset.header} while it detects something."] if reset else []),
        expected_serial_events=[SerialEvent(event="READY", when="once after power-up, when setup() has finished"),
                                SerialEvent(event="NEW_GAME", when="each time a game starts")]
        + [SerialEvent(event=e, when=w) for e, w in GAMES[game]["events"]])
