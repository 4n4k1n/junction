"""Turns a project spec into wiring steps, using only the hardware database.

Deterministic on purpose: pin choices and voltages are read from hardware/facts, never written
by a model. `facts.check_safety` is run on every plan before it is shown. The sketch for a
confirmed plan is written in codegen.py.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple

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


def _signal_pin(analog: bool = False) -> str:
    return facts.catalog()[BOARD]["analog_input_preference" if analog else "digital_input_preference"][0]


def _connection_steps(component: str, target: dict, start: int, added: bool) -> List[WiringStep]:
    """One step per wire, ground first. Unplugging USB is part of the first step and plugging it back
    in part of the last, so there is no wire that could be connected under power."""
    cat = facts.catalog()
    pins, name = cat[component]["pins"], cat[component]["name"].split()[0]
    order = {"ground": 0, "supply": 1, "digital_out": 2, "analog_out": 2}
    wires = sorted((p for p in pins if pins[p]["kind"] in target), key=lambda p: order[pins[p]["kind"]])
    steps = []
    for n, pin in enumerate(wires):
        to = target[pins[pin]["kind"]]
        label = cat[BOARD]["pins"][to].get("label", to)
        shared = added and pins[pin]["kind"] == "supply"
        display = f"{name} {pin} to board {label}" + (" (shared)" if shared else "")
        spoken = f"Connect the {'new ' if added else ''}sensor's pin labelled {pin} to {label} on the board."
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
    target = {"ground": "GND", "supply": "3V3", "analog_out" if analog else "digital_out": _signal_pin(analog)}
    return _connection_steps(spec.input_component, target, 0, added=False)


def plan_extension(steps: List[WiringStep], component: str) -> List[WiringStep]:
    """Wiring steps that add one more digital sensor to a finished build. The existing wiring stays.
    The caller must run facts.check_safety on the old and new steps together."""
    used = {s.connection.to_pin.pin for s in steps if s.connection}
    free = [p for p in facts.catalog()[BOARD]["digital_input_preference"] if p not in used]
    if not free:
        raise ValueError("no free digital input pin left")
    return _connection_steps(component, {"ground": "GND", "supply": "3V3", "digital_out": free[0]}, len(steps), added=True)


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
