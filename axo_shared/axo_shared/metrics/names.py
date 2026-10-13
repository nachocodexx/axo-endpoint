"""Metric names and dimension keys shared by every producer (endpoint, VEM,
Control Plane) and consumer (dashboards, scaling policies, billing), so both
sides always spell them the same way."""

# ── Jobs (milliseconds) ───────────────────────────────────────────────────────
JOB_TOTAL_MS = "job.total_ms"
JOB_WAIT_MS = "job.wait_ms"            # per layer, see DIM_LAYER
JOB_EXEC_MS = "job.exec_ms"
JOB_COLD_START_MS = "job.cold_start_ms"

# ── Virtual environments ──────────────────────────────────────────────────────
VE_WAKEUP_MS = "ve.wakeup_ms"

# ── Queues ────────────────────────────────────────────────────────────────────
QUEUE_LENGTH = "queue.length"
QUEUE_FULLNESS_PCT = "queue.fullness_pct"

# ── Requests (counters: each observation is a count, read StatSummary.sum) ───
REQUESTS_RECEIVED = "requests.received"
REQUESTS_REJECTED = "requests.rejected"
REQUESTS_TIMED_OUT = "requests.timed_out"
REQUESTS_RETRIED = "requests.retried"

# ── Resources (percent) ───────────────────────────────────────────────────────
RESOURCE_CPU_PCT = "resource.cpu_pct"
RESOURCE_RAM_PCT = "resource.ram_pct"

# ── Dimension keys ────────────────────────────────────────────────────────────
DIM_VE = "ve"
DIM_ENDPOINT = "endpoint"
DIM_FUNCTION = "function"
DIM_FUNCTION_VERSION = "function_version"
DIM_PRINCIPAL = "principal"            # user id or API key id
DIM_OUTCOME = "outcome"
DIM_LAYER = "layer"                    # control_plane | vem | endpoint | runner
DIM_QUEUE = "queue"                    # which queue inside a component, e.g. dispatcher, outbox

# Layer values for DIM_LAYER and MetricSummaryReported.component_kind.
LAYER_CONTROL_PLANE = "control_plane"
LAYER_VEM = "vem"
LAYER_ENDPOINT = "endpoint"
LAYER_RUNNER = "runner"
