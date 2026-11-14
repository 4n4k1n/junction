"""Turns a project spec into wiring steps, using only the hardware database.

Deterministic on purpose: pin choices and voltages are read from hardware/facts, never written
by a model. `facts.check_safety` is run on every plan before it is shown. The sketch for a
confirmed plan is written in codegen.py.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from . import facts
from .models import Behavior, Connection, PinRef, ProjectSpec, Provenance, Sketch, WiringStep

BOARD = "uno-q"

def clap_light_spec() -> ProjectSpec:
    return ProjectSpec(title="Clap-activated light", summary="A clap toggles the board's built-in LED.",
                       behavior=Behavior.toggle_on_event, board=BOARD, input_component="ky-037",
                       components=[BOARD, "ky-037"])


def make_sketch(code: str, provenance: Provenance, filename: str = "learnlens_build.ino") -> Sketch:
    return Sketch(filename=filename, code=code, provenance=provenance,
                  sha256=hashlib.sha256(code.encode()).hexdigest())


def level_meter_spec() -> ProjectSpec:
    return ProjectSpec(title="Sound level meter", summary="The louder it gets, the more of the LED matrix lights up.",
                       behavior=Behavior.level_meter, board=BOARD, input_component="ky-037", output="led_matrix",
                       components=[BOARD, "ky-037"])


def _power(component: str) -> Dict[str, str]:
    """A module's ground and supply pins, on the board's ground and 3.3 V pins."""
    pins = facts.catalog()[component]["pins"]
    board = {"ground": "GND", "supply": "3V3"}
    return {p: board[pins[p]["kind"]] for p in pins if pins[p]["kind"] in board}


def _signal_pin(analog: bool = False) -> str:
    return facts.catalog()[BOARD]["analog_input_preference" if analog else "digital_input_preference"][0]


def _connection_steps(component: str, target: Dict[str, str], start: int, added: bool) -> List[WiringStep]:
    """One step per wire in `target` (module pin -> board pin), ground first. Unplugging USB is part of
    the first step and plugging it back in part of the last, so no wire is connected under power."""
    cat = facts.catalog()
    pins, name = cat[component]["pins"], cat[component]["name"].split()[0]
    order = {"ground": 0, "supply": 1, "digital_out": 2, "analog_out": 2}
    wires = sorted(target, key=lambda p: order[pins[p]["kind"]])  # stable: signals keep their order
    steps = []
    for n, pin in enumerate(wires):
        to = target[pin]
        label = cat[BOARD]["pins"][to].get("label", to)
        shared = added and pins[pin]["kind"] == "supply"
        # Pins without a printed label (a middle pin, a loose wire) are described instead of named.
        display = f"{name} {pins[pin].get('show', pin)} to board {label}" + (" (shared)" if shared else "")
        spoken = (f"Connect the {'new ' if added else ''}sensor's {pins[pin].get('say', 'pin labelled ' + pin)} "
                  f"to {label} on the board.")
        if shared:
            spoken += (" The board has only one 3.3 volt pin. Share it with the first sensor, for example on a "
                       "breadboard rail.")
        if n == 0:
            display = "Unplug USB. " + display
            spoken = "Unplug the board's USB cable first. Then: " + spoken
        if n == len(wires) - 1:
            display += ". Plug USB in"
            spoken += " Then plug the USB cable back in" + ("." if added else " and check that the sensor's power LED lights up.")
        steps.append(WiringStep(
            index=start + n, display_text=display, spoken_text=spoken,
            connection=Connection(from_pin=PinRef(component=component, pin=pin),
                                  to_pin=PinRef(component=BOARD, pin=to)),
            fact_ids=[pins[pin]["fact"], cat[BOARD]["pins"][to]["fact"]]))
    return steps


def plan_wiring(spec: ProjectSpec) -> List[WiringStep]:
    """Wiring steps for a sensor on the board: its digital output, or for a level meter its analog
    output. The caller must run facts.check_safety."""
    analog = spec.behavior == Behavior.level_meter
    pins = facts.catalog()[spec.input_component]["pins"]
    signal = next(p for p in pins if pins[p]["kind"] == ("analog_out" if analog else "digital_out"))
    return _connection_steps(spec.input_component, {**_power(spec.input_component), signal: _signal_pin(analog)},
                             0, added=False)


def plan_extension(steps: List[WiringStep], component: str) -> List[WiringStep]:
    """Wiring steps that add one more sensor to a finished build: its ground, its supply and every
    signal the hardware database lists for it under `add_on`, each on the next free board input of
    its kind. The existing wiring stays. The caller must run facts.check_safety on the old and new
    steps together."""
    cat = facts.catalog()
    part = cat[component]
    if not part.get("add_on"):
        raise ValueError(f"{component} cannot be added to a build")
    used = {s.connection.to_pin.pin for s in steps if s.connection}
    target = _power(component)
    for signal in part["add_on"]["signals"]:
        analog = part["pins"][signal]["kind"] == "analog_out"
        free = [p for p in cat[BOARD]["analog_input_preference" if analog else "digital_input_preference"]
                if p not in used]
        if not free:
            raise ValueError(f"no free {'analog' if analog else 'digital'} input pin left")
        target[signal] = free[0]
        used.add(free[0])
    return _connection_steps(component, target, len(steps), added=True)


def render_sketch(spec: ProjectSpec) -> Sketch:
    """The rule-based sketch for a spec (no AI), used by the offline POST /builds path."""
    from .codegen import build_context, template_sketch  # codegen builds on this module's plan

    return make_sketch(template_sketch(build_context(spec, plan_wiring(spec))).source_code, Provenance.rules)


class MockPlanner:
    """Offline path for POST /builds: the clap-light project, whatever the goal or the photo says."""
    provenance = Provenance.mock

    def plan(self, goal: str, image: Optional[bytes] = None) -> Tuple[List[str], List[WiringStep]]:
        spec = clap_light_spec()
        return spec.components, plan_wiring(spec)

    def sketch(self, goal: str, steps: List[WiringStep]) -> Sketch:
        return render_sketch(clap_light_spec())
