"""Build-assistant contract: hardware facts, guided session, wiring plan, sketch, approval and deploy jobs.

Shared with the iOS app and the Mac deploy agent through shared/schemas/build-api.schema.json
(regenerate with scripts/export_schemas.py).
"""
from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------- Hardware facts (read-only, from hardware/facts/*.json, never from the LLM) ----------

class FactConfidence(str, Enum):
    official_doc = "official_doc"  # documentation from the maker of the part
    vendor_doc = "vendor_doc"      # one vendor's documentation of a generic module: confirm on the part
    secondary = "secondary"        # forums, third-party docs
    unverified = "unverified"      # no primary source: measure before relying on it


class HardwareFact(BaseModel):
    id: str
    component: str
    statement: str
    source: str = ""
    confidence: FactConfidence


# ---------- Plan ----------

class Provenance(str, Enum):
    mock = "mock"
    llm = "llm"
    rules = "rules"  # built deterministically from the hardware database


class PinRef(BaseModel):
    component: str
    pin: str


class Connection(BaseModel):
    from_pin: PinRef
    to_pin: PinRef


class WiringStep(BaseModel):
    index: int
    display_text: str                       # short, for the glasses
    spoken_text: str = ""
    connection: Optional[Connection] = None  # None for non-wiring steps (e.g. "unplug USB")
    fact_ids: List[str] = []                 # hardware facts this step relies on


class Sketch(BaseModel):
    filename: str
    code: str
    sha256: str            # approval and flashing are bound to this hash
    provenance: Provenance


# ---------- Build state (owned by the backend) ----------

class BuildStatus(str, Enum):
    wiring = "wiring"                        # showing wiring steps
    awaiting_approval = "awaiting_approval"  # sketch generated, nothing queued
    approved = "approved"                    # deploy job waiting for the Mac agent
    deploying = "deploying"                  # agent claimed the job
    awaiting_test = "awaiting_test"          # flashed; user tests the behavior
    done = "done"
    failed = "failed"
    rejected = "rejected"


class DeployEvent(BaseModel):
    """One status line from the deploy agent while it works on a job."""
    stage: str    # validate | compile | device | approval | upload | logs | runtime (one line read from the board)
    status: str   # started | ok | failed
    message: str = ""
    ts: float = 0.0


class Diagnostic(BaseModel):
    file: str
    line: int
    column: int
    severity: str  # error | warning
    message: str


class DeployResult(BaseModel):
    compile_ok: bool
    upload_ok: bool      # true only when the upload tool itself exited successfully
    log: str = ""
    mock: bool = False   # true when the agent ran in --mock mode: no tool was run, no board was flashed
    failed_stage: str = ""                 # validate | compile | device | approval | upload
    diagnostics: List[Diagnostic] = []     # compiler errors and warnings
    port: str = ""
    runtime_log: List[str] = []            # lines actually read from the board after the upload
    observed_events: List[str] = []        # expected log lines that were seen in runtime_log


class Build(BaseModel):
    build_id: str
    goal: str
    status: BuildStatus
    provenance: Provenance
    components: List[str]
    photo_received: bool = False        # a photo came with the goal (the mock planner ignores it)
    hardware_facts: List[HardwareFact]  # facts
    wiring_steps: List[WiringStep]      # AI suggestion, validated against the facts' pin lists
    sketch: Optional[Sketch] = None     # AI suggestion
    deploy: Optional[DeployResult] = None
    deploy_events: List[DeployEvent] = []   # live status from the agent, newest last
    expected_serial_events: List[str] = []  # log lines the sketch should print, for the test step
    test_passed: Optional[bool] = None
    error: str = ""


# ---------- API payloads ----------

class CreateBuildRequest(BaseModel):
    goal: str


class ApproveRequest(BaseModel):
    sketch_sha256: str


class TestResultRequest(BaseModel):
    passed: bool
    note: str = ""


class ClaimRequest(BaseModel):
    agent_id: str
    mock: bool = False  # the agent runs with --mock: it will not touch a board


class DeployJob(BaseModel):
    job_id: str
    build_id: str
    sketch: Sketch
    fqbn: Optional[str] = None           # from hardware/facts; the agent only accepts its own fixed FQBN
    approval_token: str = ""             # short-lived, signed by the backend when the user approved
    expected_serial_events: List[str] = []


# ---------- Guided session: photo + instruction -> confirmed parts -> wiring steps ----------

class DetectedComponent(BaseModel):
    component_id: str  # an id from the hardware database, or "unknown"
    label: str         # what the vision model thinks it sees, in its own words
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""


class Behavior(str, Enum):
    toggle_on_event = "toggle_on_event"  # each trigger flips the output
    on_while_active = "on_while_active"  # output follows the input
    level_meter = "level_meter"          # the louder the input, the more of the LED matrix lights up
    custom = "custom"                    # a finished build that was developed further (see custom_behavior)
    unsupported = "unsupported"


class ProjectSpec(BaseModel):
    title: str
    summary: str
    behavior: Behavior
    board: str
    input_component: Optional[str] = None
    output: str = "builtin_led"
    components: List[str] = []
    supported: bool = True
    unsupported_reason: str = ""
    custom_behavior: str = ""   # what the whole build should do after it was developed further
    requests: List[str] = []    # the user's follow-up requests, in order


class InventoryItem(BaseModel):
    """Hardware the user has confirmed, with the checks they did on the part itself."""
    component_id: str
    confirmations: List[str] = []


class ConfirmationKind(str, Enum):
    presence = "presence"  # vision was not sure this part is on the desk
    variant = "variant"    # only the user can check this on the physical part (e.g. pin labels)


class PendingConfirmation(BaseModel):
    id: str
    component_id: str
    kind: ConfirmationKind
    question: str
    reason: str


class WiringReview(BaseModel):
    observations: List[str]
    possible_issues: List[str]
    electrically_verified: Literal[False] = False  # a photo cannot prove a connection
    disclaimer: str


class GuideState(str, Enum):
    awaiting_analysis = "awaiting_analysis"    # waiting for a photo or a confirmed inventory
    needs_confirmation = "needs_confirmation"  # questions in pending_confirmations
    wiring = "wiring"                          # confirm each step with /confirm-step
    wiring_complete = "wiring_complete"        # every step confirmed: POST /generate
    code_generated = "code_generated"          # build_id carries the sketch awaiting approval
    unsupported = "unsupported"                # project outside the MVP hardware
    blocked = "blocked"                        # the plan failed a safety check


class GuideStatus(BaseModel):
    session_id: str
    state: GuideState
    instruction: str
    display_text: str   # short, for the glasses
    spoken_text: str = ""
    analysis_source: str = ""  # "llm" | "mock" | "fallback" | "" before the first analysis
    detected_components: List[DetectedComponent] = []  # AI suggestion
    confirmed_inventory: List[InventoryItem] = []      # user statement
    pending_confirmations: List[PendingConfirmation] = []
    project: Optional[ProjectSpec] = None
    hardware_facts: List[HardwareFact] = []            # hardware database
    wiring_steps: List[WiringStep] = []                # built by rules from the hardware database
    current_step: Optional[int] = None
    confirmed_steps: List[int] = []
    review: Optional[WiringReview] = None
    safety_problems: List[str] = []
    generation_progress: List[str] = []  # what /generate is doing right now (last entry) and did before
    clarification: str = ""              # a question back to the user before /extend can go on
    suggestions: List[str] = []          # 1-3 example answers to that question
    build_id: Optional[str] = None


# ---------- Code generation (POST /sessions/{id}/generate) ----------

class InputPin(BaseModel):
    """One sensor signal the sketch may read."""
    component: str
    name: str
    header: str                  # "D2" or "A0"
    number: Optional[int] = None  # Arduino pin number of a digital header
    kind: str                    # digital | analog
    active_level: str = ""       # digital: level on the pin while the sensor reports its event


class CodegenContext(BaseModel):
    """Everything the generator may use. Built from the confirmed wiring plan and the hardware
    database, never from model output."""
    project: ProjectSpec
    board_name: str
    fqbn: str
    sensor_name: str
    signal_kind: str = "digital"  # digital (digitalRead on a D pin) | analog (analogRead on an A pin)
    output: str = "builtin_led"   # builtin_led | led_matrix
    sensor_pin: str          # header name, e.g. "D2" or "A0"
    sensor_pin_number: Optional[int] = None  # Arduino pin number of a digital header
    sensor_active_level: str = Field(default="HIGH", pattern="^(HIGH|LOW)$")  # digital: level while the sensor reports an event
    debounce_ms: int = Field(default=300, ge=20, le=2000)                     # digital
    sample_window_ms: int = Field(default=25, ge=5, le=100)                   # analog: one loudness measurement
    led_symbol: str          # e.g. LED_BUILTIN
    led_on_level: str        # level that turns the LED on
    matrix_type: str = ""    # e.g. Arduino_LED_Matrix
    matrix_rows: int = 0
    matrix_columns: int = 0
    matrix_max_brightness: int = 0
    inputs: List[InputPin] = []   # every signal in the confirmed wiring, the first one is sensor_pin
    custom_behavior: str = ""     # set when a finished build is developed further: free-form behaviour
    previous_code: str = ""       # the sketch that was running before the change
    log_object: str          # e.g. Monitor
    log_setup: List[str]     # statements that start logging
    allowed_includes: List[str]
    required_events: List[str]
    facts: List[str]         # board facts quoted to the model


class SerialEvent(BaseModel):
    event: str
    when: str


class GeneratedSketch(BaseModel):
    """What the model must return (and what the template returns)."""
    source_code: str = Field(min_length=1)
    explanation: str
    display_summary: str = ""  # one or two short sentences for the glasses display
    assumptions: List[str]
    expected_serial_events: List[SerialEvent]


class CodeIssue(BaseModel):
    check: str            # schema | static | compile
    rule: str
    message: str
    line: Optional[int] = None


class CompileStatus(str, Enum):
    ok = "ok"
    failed = "failed"
    not_run = "not_run"   # no arduino-cli or board core on this machine


class CompileReport(BaseModel):
    status: CompileStatus
    fqbn: str
    command: List[str] = []
    log: str = ""
    detail: str = ""


class GenerationAttempt(BaseModel):
    attempt: int
    kind: str             # generate | repair | template
    issues: List[CodeIssue] = []
    compile: Optional[CompileReport] = None
    project_dir: str = ""


class GenerateRequest(BaseModel):
    sensor_active_level: Optional[str] = Field(default=None, pattern="^(HIGH|LOW)$")
    debounce_ms: Optional[int] = Field(default=None, ge=20, le=2000)
    sample_window_ms: Optional[int] = Field(default=None, ge=5, le=100)  # level meter only


class ExtendRequest(BaseModel):
    """Develop a finished build further: add a known part, or change what it does."""
    instruction: str = Field(min_length=1)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class StepAnswer(BaseModel):
    """Answer to a question the user asked during the build."""
    question: str
    answer: str               # short enough for the glasses display
    source: str               # llm | facts (no AI: the verified facts for the current step)
    step_index: Optional[int] = None


class GenerationResult(BaseModel):
    session_id: str
    build_id: str
    source: str           # llm | template
    sketch: Sketch
    explanation: str
    display_summary: str  # how the build works, short enough for the glasses display
    assumptions: List[str]
    expected_serial_events: List[SerialEvent]
    parameters: dict
    attempts: List[GenerationAttempt]
    repairs_used: int
    compile: CompileReport
    project_dir: str
    flashed: Literal[False] = False             # generation never uploads
    hardware_verified: Literal[False] = False   # nothing here ran on a board


class CreateGuideSessionRequest(BaseModel):
    instruction: str = Field(min_length=1)
    confirmed_inventory: List[InventoryItem] = []


class ConfirmStepRequest(BaseModel):
    step_index: int
    confirmed: bool = True
    note: str = ""


API_MODELS = [
    Build, CreateBuildRequest, ApproveRequest, TestResultRequest, ClaimRequest, DeployJob, DeployResult,
    DeployEvent, GuideStatus, CreateGuideSessionRequest, ConfirmStepRequest, GenerateRequest, GenerationResult,
    ExtendRequest, AskRequest, StepAnswer,
]
