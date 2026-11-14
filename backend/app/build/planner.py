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


def _signal_pin() -> str:
    return facts.catalog()[BOARD]["digital_input_preference"][0]


def plan_wiring(spec: ProjectSpec) -> List[WiringStep]:
    """Wiring steps for a digital sensor on the board. The caller must run facts.check_safety."""
    cat = facts.catalog()
    sensor, name = spec.input_component, cat[spec.input_component]["name"].split()[0]
    pins = cat[sensor]["pins"]
    target = {"ground": "GND", "supply": "3V3", "digital_out": _signal_pin()}
    order = {"ground": 0, "supply": 1, "digital_out": 2}

    steps = [WiringStep(index=0, display_text="Unplug the board's USB cable",
                        spoken_text="Unplug the USB cable before you wire anything.")]
    for pin in sorted((p for p in pins if pins[p]["kind"] in target), key=lambda p: order[pins[p]["kind"]]):
        to = target[pins[pin]["kind"]]
        label = cat[BOARD]["pins"][to].get("label", to)
        steps.append(WiringStep(
            index=len(steps), display_text=f"{name} {pin} to board {label}",
            spoken_text=f"Connect the sensor pin labelled {pin} to {label} on the board.",
            connection=Connection(from_pin=PinRef(component=sensor, pin=pin),
                                  to_pin=PinRef(component=BOARD, pin=to)),
            fact_ids=[pins[pin]["fact"], cat[BOARD]["pins"][to]["fact"]]))
    steps.append(WiringStep(index=len(steps), display_text="Plug USB back in. Sensor power LED on?",
                            spoken_text="Plug the USB cable back in and check that the sensor's power LED lights up."))
    return steps


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
