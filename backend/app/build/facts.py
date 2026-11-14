"""Hardware database, loaded from hardware/facts/*.json.

The only source of pin names, voltages and polarities. Nothing here comes from a model:
every value cites a fact, and every fact cites its source and confidence.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .models import HardwareFact, PinRef, WiringStep

FACTS_DIR = Path(__file__).resolve().parents[3] / "hardware" / "facts"


@lru_cache(maxsize=1)
def catalog() -> Dict[str, dict]:
    return {c["component"]: c for c in (json.loads(p.read_text()) for p in sorted(FACTS_DIR.glob("*.json")))}


def facts_for(components: List[str]) -> List[HardwareFact]:
    return [HardwareFact(component=c, **f) for c in components for f in catalog()[c]["facts"]]


def fqbn(component: str) -> Optional[str]:
    return (catalog()[component].get("fqbn") or {}).get("value")


def _pin(ref: PinRef) -> Optional[dict]:
    return (catalog().get(ref.component) or {}).get("pins", {}).get(ref.pin)


def validate_plan(steps: List[WiringStep]) -> List[str]:
    """Check that a plan only names components, pins and facts that exist. Empty means usable."""
    cat = catalog()
    fact_ids = {f["id"] for c in cat.values() for f in c["facts"]}
    problems = []
    for s in steps:
        for ref in ([s.connection.from_pin, s.connection.to_pin] if s.connection else []):
            if ref.component not in cat:
                problems.append(f"step {s.index}: unknown component {ref.component}")
            elif ref.pin not in cat[ref.component]["pins"]:
                problems.append(f"step {s.index}: {ref.component} has no pin {ref.pin}")
        for fid in s.fact_ids:
            if fid not in fact_ids:
                problems.append(f"step {s.index}: unknown fact {fid}")
    return problems


def check_safety(steps: List[WiringStep]) -> List[str]:
    """Electrical checks on a plan whose connections all run from a module pin to a board pin.

    A module's outputs are assumed to swing up to its supply rail, so every signal is checked
    against the highest voltage the chosen supply pin can carry. Empty means no violation.
    """
    problems = validate_plan(steps)
    if problems:
        return problems

    links = [(s.index, s.connection.from_pin, s.connection.to_pin) for s in steps if s.connection]
    # Highest voltage each module is powered with.
    supply_max: Dict[str, float] = {}
    for index, src, dst in links:
        a, b = _pin(src), _pin(dst)
        if a["kind"] != "supply":
            continue
        if b["kind"] != "power_out":
            problems.append(f"step {index}: {src.component} {src.pin} must go to a power pin, not {dst.pin}")
        elif not a["min_v"] <= b["nominal_v"] <= a["max_v"]:
            problems.append(f"step {index}: {dst.pin} ({b['nominal_v']} V) is outside the "
                            f"{a['min_v']}-{a['max_v']} V supply range of {src.component}")
        else:
            supply_max[src.component] = b["max_v"]

    for index, src, dst in links:
        a, b = _pin(src), _pin(dst)
        if a["kind"] == "ground" and b["kind"] != "ground":
            problems.append(f"step {index}: {src.component} {src.pin} must go to GND, not {dst.pin}")
        if a["kind"] in ("digital_out", "analog_out"):
            if b["kind"] != "gpio":
                problems.append(f"step {index}: {src.component} {src.pin} is a signal and cannot go to {dst.pin}")
            elif src.component not in supply_max:
                problems.append(f"step {index}: {src.component} has no valid supply, so the voltage on "
                                f"{src.pin} is unknown")
            elif supply_max[src.component] > b["max_input_v"]:
                problems.append(f"step {index}: {src.pin} can reach {supply_max[src.component]} V but "
                                f"{dst.component} {dst.pin} tolerates {b['max_input_v']} V")
    return problems
