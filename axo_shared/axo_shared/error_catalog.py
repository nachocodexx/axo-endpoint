"""Every error the platform can show a person, in one place: its number,
name, which part of the platform raises it, what it means and what to do.

The Control Plane serves this list (``GET /errors``) and the UI's error page
reads it; every error a person sees carries its ``error`` (name) and
``error_code`` (number) so the UI can link straight to its entry.

Each project has a test checking that each of its error classes is listed
here with the same number and name, and that every entry has a description
and a hint -- add the entry together with the class.

Number ranges: 0-8xxx endpoint and shared (by area), 91xx messaging, 92xx
metrics, 93xx axo_storage, 94xx active objects, 95xx Control Plane, 96xx VE manager (VEM),
99xx the web UI itself (it can't reach the platform, or got an answer it
can't read).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

ENDPOINT = "endpoint"
SHARED = "shared"
STORAGE = "storage"
OBJECTS = "objects"
CONTROL_PLANE = "control_plane"
VEM = "vem"
UI = "ui"

COMPONENTS = {
    ENDPOINT: "Endpoint (runs your functions)",
    SHARED: "Platform messaging and data",
    STORAGE: "Bucket storage",
    OBJECTS: "Active objects",
    CONTROL_PLANE: "Control Plane (accounts, VEs, billing)",
    VEM: "VE manager",
    UI: "Web app",
}


@dataclass(frozen=True)
class ErrorInfo:
    code: int
    name: str
    component: str
    description: str
    hint: str
    http_status: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _e(code: int, name: str, component: str, description: str, hint: str,
       http_status: Optional[int] = None) -> ErrorInfo:
    return ErrorInfo(code, name, component, description, hint, http_status)


CATALOG: List[ErrorInfo] = [
    # ── endpoint: requests (1xxx) ────────────────────────────────────────────
    _e(0, "AXO_ERROR", SHARED, "An unexpected error inside the platform.",
       "Try again. If it keeps happening, contact support with the time it happened."),
    _e(1001, "MISSING_FIELD", ENDPOINT, "The request is missing a field the endpoint needs.",
       "Check the request; the message names the missing field."),
    _e(1002, "INVALID_FIELD", ENDPOINT, "A field of the request has a value the endpoint can't use.",
       "Check the value named in the message (type and allowed range)."),
    _e(1003, "UNKNOWN_OPERATION", ENDPOINT, "The endpoint doesn't know the operation it was asked for.",
       "The client and the endpoint may be on different versions; update the client."),
    # ── endpoint: functions and jobs (2xxx) ──────────────────────────────────
    _e(2000, "REGISTRY_ERROR", ENDPOINT, "The endpoint's function list could not be read or changed.",
       "Try again in a moment."),
    _e(2001, "FUNCTION_NOT_FOUND", ENDPOINT, "No function with that id and version is registered in this VE.",
       "Check the function and version, or register the function first."),
    _e(2002, "JOB_NOT_FOUND", ENDPOINT, "No job with that id is known here.",
       "Check the job id. Very old job results may no longer be kept."),
    _e(2003, "INVALID_STATE_TRANSITION", ENDPOINT, "The job or function can't move to that state from where it is.",
       "Refresh the page to see its current state, then try again."),
    _e(2005, "FOREIGN_VIRTUAL_ENVIRONMENT", ENDPOINT,
       "An endpoint of another VE tried to join this VE's cluster and was refused.",
       "Nothing to do: endpoints only ever work inside their own VE."),
    _e(2006, "FUNCTION_VERSION_EXISTS", ENDPOINT, "That version of the function is already registered.",
       "Register a new version instead; existing versions never change."),
    _e(2007, "ENDPOINT_DRAINING", ENDPOINT,
       "This endpoint is being removed or stopped and takes no new jobs.",
       "Nothing to do: the job is sent to another endpoint. If you see this often, check the VE's endpoints."),
    # ── endpoint: running (3xxx) ─────────────────────────────────────────────
    _e(3000, "RUNTIME_ERROR", ENDPOINT, "The function could not be run.",
       "Try again. If it keeps happening, check the function's runtime settings."),
    _e(3001, "WORKER_CRASHED", ENDPOINT, "The worker process running the function stopped unexpectedly.",
       "Often the function used too much memory. Check its memory use, or run it in a container with more."),
    _e(3002, "FUNCTION_EXEC_FAILED", ENDPOINT, "Your function raised an error while running.",
       "Open the job to read the error and fix the function's code. This attempt is billed."),
    _e(3003, "INVOCATION_FAILED", ENDPOINT, "The endpoint could not hand the job to a runner.",
       "Try again in a moment."),
    _e(3004, "CONTAINER_ERROR", ENDPOINT, "Something went wrong with the function's container.",
       "Try again. If it keeps happening, check the function's container settings and requirements."),
    _e(3005, "CONTAINER_BOOTSTRAP_ERROR", ENDPOINT, "The function's container did not start in time.",
       "Check the function's requirements (a slow or failing install is the usual cause)."),
    _e(3006, "CONTAINER_CRASH_ERROR", ENDPOINT, "The function's container stopped while running a job.",
       "Often it ran out of memory. Give the runner more memory or reduce what the function loads."),
    _e(3007, "JOB_TIMEOUT", ENDPOINT, "The job ran longer than the function's maximum duration and was stopped.",
       "Make the function faster, or raise its maximum duration. This attempt is billed."),
    _e(3008, "JOB_CANCELLED", ENDPOINT, "The job was cancelled.",
       "Nothing to do. Cancelled jobs are not billed."),
    _e(3009, "QUEUE_WAIT_TIMEOUT", ENDPOINT, "The job waited longer than its max_wait for a free runner.",
       "Raise max_wait, allow more runners (max_concurrency), or add endpoints. Not billed."),
    _e(3010, "RUNNER_BUDGET_EXHAUSTED", ENDPOINT, "The endpoint has no CPU / memory left for another runner.",
       "Use a bigger endpoint size, add endpoints, or lower the runner size."),
    # ── endpoint: queues (4xxx) ──────────────────────────────────────────────
    _e(4001, "QUEUE_FULL", ENDPOINT, "The endpoint's request queue is full.",
       "Try again in a moment, or add endpoints to the VE."),
    _e(4002, "DISPATCHER_CLOSED", ENDPOINT, "The endpoint is shutting down and takes no requests.",
       "Try again; another endpoint will answer."),
    _e(4003, "STORAGE_FAILURE", ENDPOINT, "The endpoint could not read or write its own records.",
       "Try again in a moment."),
    # ── wire (5xxx) ──────────────────────────────────────────────────────────
    _e(5000, "WIRE_ERROR", SHARED, "A message between platform parts could not be read.",
       "The parts may be on different versions; update them."),
    _e(5001, "MALFORMED_FRAME_COUNT", SHARED, "A message between platform parts had the wrong shape.",
       "The parts may be on different versions; update them."),
    _e(5002, "MALFORMED_ENVELOPE", SHARED, "A message's header could not be read.",
       "The parts may be on different versions; update them."),
    _e(5003, "MALFORMED_METADATA", SHARED, "A reply's details could not be read.",
       "The parts may be on different versions; update them."),
    _e(5004, "MALFORMED_EVENT_ENVELOPE", SHARED, "An event sent between platform parts could not be read.",
       "The parts may be on different versions; update them."),
    # ── cluster (6xxx) ───────────────────────────────────────────────────────
    _e(6001, "LEADER_UNREACHABLE", ENDPOINT, "The VE's lead endpoint could not be reached.",
       "Usually brief while the endpoints choose a new leader; try again in a few seconds."),
    _e(6002, "STALE_TERM", ENDPOINT, "An endpoint received an out-of-date update and ignored it.",
       "Nothing to do; the endpoints correct this themselves."),
    _e(6003, "ELECTION_IN_PROGRESS", ENDPOINT, "The endpoints are choosing a leader.",
       "Try again in a few seconds."),
    _e(6004, "REPLICATION_REJECTED", ENDPOINT, "An endpoint refused an update from the leader.",
       "Nothing to do; the endpoints correct this themselves."),
    _e(6005, "CONCURRENCY_LEDGER_NOT_READY", ENDPOINT,
       "The lead endpoint is still collecting which runners exist.",
       "Try again in a few seconds."),
    # ── dataio (7xxx) ────────────────────────────────────────────────────────
    _e(7000, "DATAIO_ERROR", ENDPOINT, "Your function's read or write of bucket data failed.",
       "Read the message for the cause; check the bucket and object names."),
    _e(7001, "UNKNOWN_IO_OP", ENDPOINT, "The function asked for a data operation that doesn't exist.",
       "Update the axo library used by the function."),
    _e(7002, "UNKNOWN_IO_REF_KIND", ENDPOINT, "The function pointed at data in a way the endpoint doesn't know.",
       "Use IORef(bucket=..., name=...) to point at bucket data."),
    _e(7003, "UNKNOWN_IO_FORMAT", ENDPOINT, "The data format asked for doesn't exist.",
       "Use one of: raw, pickle, csv, npy."),
    _e(7004, "IO_PATH_TRAVERSAL", ENDPOINT, "The object name tried to reach outside its place.",
       "Use a plain object name without '..'."),
    _e(7005, "IO_NOT_FOUND", ENDPOINT, "The object the function tried to read doesn't exist.",
       "Check the bucket and object names (and version, if you gave one)."),
    _e(7006, "IO_BACKEND_ERROR", ENDPOINT, "Bucket storage refused or failed the function's request.",
       "The message starts with the storage error's name; look that error up for details."),
    _e(7007, "DATAIO_TIMEOUT", ENDPOINT, "The function's data request got no answer in time.",
       "Try again; for very large objects read them in chunks (iter_chunks)."),
    _e(7008, "MALFORMED_IO_FRAME", ENDPOINT, "A data message between the function and its endpoint was broken.",
       "Update the axo library used by the function."),
    _e(7009, "DATAIO_UNAVAILABLE", ENDPOINT, "Bucket data can't be used from here.",
       "Bucket data works only inside a job run by an endpoint, not in a direct call."),
    _e(7010, "IO_FORMAT_UNAVAILABLE", ENDPOINT, "The format needs a library the function doesn't have.",
       "Add the library to the function's requirements (for csv: pandas)."),
    # ── storage backend (8xxx) ───────────────────────────────────────────────
    _e(8000, "STORAGE_ERROR", SHARED, "A local file or record could not be read or written.",
       "Try again in a moment."),
    _e(8001, "STORAGE_PATH_TRAVERSAL", SHARED, "A file name tried to reach outside its folder.",
       "Use a plain name without '..'."),
    _e(8002, "STORAGE_BACKEND_IO_ERROR", SHARED, "Reading or writing a local file failed.",
       "Check the disk (space, permissions)."),
    _e(8101, "RESULT_HASH_MISMATCH", ENDPOINT, "A copy of a job result was damaged on the way and was dropped.",
       "Nothing to do; a good copy is sent again."),
    # ── messaging / metrics (91xx, 92xx) ─────────────────────────────────────
    _e(9101, "JOURNAL_IO_ERROR", SHARED, "The local outbox of unsent messages could not be read or written.",
       "Check the disk of that service; messages are kept and resent once it works."),
    _e(9102, "MALFORMED_ACK", SHARED, "A delivery receipt could not be read.",
       "Nothing to do; the message is sent again."),
    _e(9103, "TRANSPORT_SEND_FAILED", SHARED, "A message could not be sent right now.",
       "Nothing to do; it is kept and resent."),
    _e(9201, "INCOMPATIBLE_SKETCH", SHARED, "Two sets of statistics could not be combined.",
       "The services may be on different versions; update them."),
    _e(9202, "INVALID_METRIC_VALUE", SHARED, "A measured value was negative or not a number and was skipped.",
       "Nothing to do; it doesn't affect other values."),
    _e(9203, "INVALID_TIER_SPEC", SHARED, "The statistics keep-times setting could not be read.",
       "Write it like 1m:7d,15m:90d,1d:forever."),
    # ── axo_storage (93xx) ───────────────────────────────────────────────────
    _e(9301, "STORAGE_UNAUTHENTICATED", STORAGE, "Bucket storage did not accept the caller's credentials.",
       "A platform setting is wrong (VE or service token); contact your platform admin."),
    _e(9302, "STORAGE_ACCESS_DENIED", STORAGE, "You don't have access to this bucket.",
       "Ask the bucket's owner to share it with you (Viewer to read, Developer to write)."),
    _e(9303, "STORAGE_ACCESS_CHECK_UNAVAILABLE", STORAGE,
       "Bucket storage couldn't check your access right now, so it refused.",
       "Try again in a moment."),
    _e(9304, "BUCKET_NOT_FOUND", STORAGE, "That bucket doesn't exist (or you can't see it).",
       "Check the name, written as owner/bucket."),
    _e(9305, "BUCKET_ALREADY_EXISTS", STORAGE, "A bucket with that name already exists.",
       "Pick another name."),
    _e(9306, "OBJECT_NOT_FOUND", STORAGE, "That object (or version) isn't in the bucket.",
       "Check the object name and version."),
    _e(9307, "OBJECT_VERSION_EXISTS", STORAGE, "That object version already exists; versions never change.",
       "Write without a version to create the next one."),
    _e(9308, "OBJECT_NOT_COMPLETE", STORAGE, "That object version is still uploading (or its upload failed).",
       "Wait for the upload to finish, or read an earlier version."),
    _e(9309, "OBJECT_ALREADY_COMPLETE", STORAGE, "That object version is complete and can't be written to.",
       "Write a new version instead."),
    _e(9310, "STORAGE_CHUNK_OUT_OF_RANGE", STORAGE, "A piece of the object was asked for that doesn't exist.",
       "Update the client; it asked for a piece past the end."),
    _e(9311, "STORAGE_CHUNK_SIZE_MISMATCH", STORAGE, "A piece of an upload had the wrong size.",
       "Retry the upload; every piece but the last must be full size."),
    _e(9312, "STORAGE_QUOTA_EXCEEDED", STORAGE, "The bucket is full (its size limit was reached).",
       "Delete old versions or ask the bucket's owner to raise its size limit."),
    _e(9313, "OBJECT_HASH_MISMATCH", STORAGE, "The uploaded data was damaged on the way.",
       "Upload it again."),
    _e(9314, "STORAGE_INVALID_REQUEST", STORAGE, "The request to bucket storage was missing a field.",
       "Update the client."),
    _e(9315, "STORAGE_BACKEND_FAILURE", STORAGE, "Bucket storage could not read or write the data.",
       "Try again in a moment."),
    _e(9316, "STORAGE_UNAVAILABLE", STORAGE, "Bucket storage did not answer in time.",
       "Try again in a moment."),
    # ── active objects (94xx) ────────────────────────────────────────────────
    _e(9401, "ACTIVE_OBJECT_NOT_FOUND", OBJECTS, "No object with that name exists in this VE.",
       "Check the name, or create the object first.", 404),
    _e(9402, "ACTIVE_OBJECT_EXISTS", OBJECTS, "An object with that name already exists in this VE.",
       "Pick another name, or use the existing object.", 409),
    _e(9403, "ACTIVE_OBJECT_STATE_CONFLICT", OBJECTS,
       "The object changed while the call ran (another endpoint saved first); the call's changes were not saved.",
       "Run the call again; it will see the latest state.", 409),
    _e(9404, "ACTIVE_OBJECT_STATE_TOO_LARGE", OBJECTS,
       "Saving the call's changes would make the object bigger than its plan allows; nothing was saved.",
       "Keep big data in a bucket and store its name on the object, or move to a plan with a larger limit.", 422),
    _e(9405, "ACTIVE_OBJECT_STATE_UNREADABLE", OBJECTS, "A saved attribute of the object is missing or can't be read.",
       "If the class changed how it stores that attribute, upgrade the object; otherwise contact support.", 500),
    _e(9406, "ACTIVE_OBJECT_STATE_BACKEND_FAILURE", OBJECTS, "The store holding object state didn't answer.",
       "Try again in a moment; the call's changes were not saved.", 503),
    _e(9407, "ACTIVE_OBJECT_ATTRIBUTE_INVALID", OBJECTS,
       "An attribute can't be saved: its name isn't allowed or its value can't be stored.",
       "Use plain names (not starting with _) and values that can be pickled; open files and sockets can't.", 422),
    _e(9408, "ACTIVE_OBJECT_CLASS_NOT_FOUND", OBJECTS, "No class with that name and version is registered in this VE.",
       "Check the class and version, or register the class first.", 404),
    _e(9409, "ACTIVE_OBJECT_METHOD_NOT_FOUND", OBJECTS, "The object's class has no callable method with that name.",
       "Only methods marked @axo_method can be called; check the name and the object's class version.", 404),
    _e(9410, "ACTIVE_OBJECT_CLASS_INVALID", OBJECTS, "The class can't be saved to the platform.",
       "Read the message: the class must subclass Axo and its code must be importable or picklable.", 422),
    _e(9411, "ACTIVE_OBJECT_CLASS_IN_USE", OBJECTS, "That class version can't be deleted: objects are on it.",
       "Upgrade those objects to a newer version (or delete them) first.", 409),
    # ── Control Plane (95xx) ─────────────────────────────────────────────────
    _e(9501, "INVALID", CONTROL_PLANE, "Something in the request isn't valid.",
       "Read the message for what to fix.", 422),
    _e(9502, "NOT_FOUND", CONTROL_PLANE, "That doesn't exist, or you don't have access to it.",
       "Check the name or link. If it's shared with you, ask its owner for access.", 404),
    _e(9503, "CONFLICT", CONTROL_PLANE, "This can't be done in the current state.",
       "Refresh to see the current state, then try again.", 409),
    _e(9504, "UNAUTHORIZED", CONTROL_PLANE, "You are not signed in, or your session ended.",
       "Sign in again.", 401),
    _e(9505, "FORBIDDEN", CONTROL_PLANE, "Your role doesn't allow this.",
       "Ask the owner (of the VE, bucket or team) for a higher role.", 403),
    _e(9506, "UNAVAILABLE", CONTROL_PLANE, "A service needed for this isn't answering right now.",
       "Try again in a moment.", 503),
    _e(9507, "LIMIT_REACHED", CONTROL_PLANE, "A limit was reached.",
       "Read the message for which limit; a bigger plan may raise it.", 429),
    _e(9508, "REQUEST_INVALID", CONTROL_PLANE, "The request has missing or wrongly typed fields.",
       "Check the fields listed with the error.", 422),
    _e(9510, "NAME_TAKEN", CONTROL_PLANE, "That user or team name is already taken.",
       "Pick another name.", 409),
    _e(9511, "VE_NAME_TAKEN", CONTROL_PLANE, "The owner already has a VE with that name.",
       "Pick another name.", 409),
    _e(9512, "REQUEST_LIMIT", CONTROL_PLANE, "This VE used every request of its plan for the current rental period.",
       "Wait for the next period (date shown in the error) or move the VE to a paid plan.", 429),
    _e(9513, "BUCKET_NAME_TAKEN", CONTROL_PLANE, "That bucket name (or alias) is already used.",
       "Pick another name.", 409),
    _e(9514, "ONE_FREE_VE", CONTROL_PLANE, "Each person can have only one Free VE.",
       "Use your Free VE, delete it first, or pick a paid plan.", 409),
    _e(9515, "PLAN_LIMIT", CONTROL_PLANE, "The plan doesn't allow that many endpoints.",
       "Use fewer endpoints or move to a bigger plan.", 422),
    _e(9516, "QUOTA_TOO_SMALL", CONTROL_PLANE, "The VE's quota can't hold the endpoints asked for.",
       "Raise the quota, use a smaller endpoint size, or fewer endpoints.", 422),
    _e(9517, "QUOTA_TOO_BIG", CONTROL_PLANE, "The size asked for is above the allowed maximum.",
       "Ask for less (the maximum is in the message).", 422),
    _e(9518, "CAPACITY_FULL", CONTROL_PLANE, "The platform has no room for that much CPU / memory right now.",
       "Try a smaller quota, or try again later.", 409),
    _e(9519, "CONFIRMATION_REQUIRED", CONTROL_PLANE, "Deleting needs you to type the exact name to confirm.",
       "Type the name exactly as shown.", 422),
    _e(9520, "RENTAL_ENDED", CONTROL_PLANE, "This VE's rental has ended (it was cancelled), so it's stopped.",
       "The Owner can resume it from the VE page; a new period starts then.", 409),
    _e(9521, "PASSWORD_CHANGE_REQUIRED", CONTROL_PLANE, "You must change your password before going on.",
       "Open Settings and set a new password.", 403),
    _e(9522, "VE_STOPPED", CONTROL_PLANE, "The VE is stopped.",
       "Start it from the VE page (Admin or Owner).", 409),
    _e(9523, "VE_WAKING", CONTROL_PLANE, "The VE was asleep and is waking up.",
       "Try again in a few seconds; jobs you submit meanwhile are kept and run once it's up.", 503),
    _e(9524, "VE_STARTING", CONTROL_PLANE, "The VE is still starting.",
       "Try again in a few seconds.", 503),
    _e(9525, "VEM_DOWN", CONTROL_PLANE, "The VE's manager isn't responding right now.",
       "It is being restarted automatically; try again in a minute.", 503),
    _e(9526, "XOLO_ERROR", CONTROL_PLANE, "The account service answered with an error.",
       "Try again in a moment.", 503),
    _e(9527, "XOLO_KEY_REFUSED", CONTROL_PLANE, "The account service refused the platform's key.",
       "A platform setting is wrong; contact your platform admin.", 503),
    _e(9528, "SETTINGS_INVALID", CONTROL_PLANE, "That setting or value doesn't exist.",
       "Pick one of the choices the settings page lists.", 422),
    _e(9529, "REPAIR_TOO_SOON", CONTROL_PLANE, "This VE was repaired a few minutes ago.",
       "A VE can be repaired once every 5 minutes; the answer says when it can be again.", 429),
    _e(9530, "REPAIR_NOT_RUNNING", CONTROL_PLANE, "Only a VE that should be running can be repaired.",
       "Start the VE first (a stopped, sleeping or deleted VE has nothing to repair).", 409),
    _e(9531, "UPLOAD_TOO_BIG", CONTROL_PLANE, "That file is too big to upload from the page.",
       "Files up to 5 GB can be uploaded from the page; bigger ones are written by a job (dataio.write_chunks).", 422),
    _e(9532, "DOWNLOAD_TOO_BIG", CONTROL_PLANE, "That's too much for one zip.",
       "A zip holds up to 2 GB and 10,000 files: select fewer, or download the files one by one.", 422),
    # ── VE manager (96xx) ────────────────────────────────────────────────────
    _e(9601, "VEM_NOT_FOUND", VEM, "That doesn't exist in this VE.",
       "Check the name or id; it may have been deleted.", 404),
    _e(9602, "VEM_FORBIDDEN", VEM, "This isn't allowed on that item.",
       "Ask the VE's Owner or an Admin.", 403),
    _e(9603, "VEM_CONFLICT", VEM, "This can't be done in the item's current state.",
       "Refresh to see the current state, then try again.", 409),
    _e(9604, "ENDPOINT_TIMEOUT", VEM, "No endpoint of the VE answered in time.",
       "Check that the VE has running endpoints, then try again.", 504),
    _e(9605, "VEM_INVALID", VEM, "Something in the request isn't valid.",
       "Read the message for what to fix.", 422),
    _e(9606, "VEM_UNAUTHORIZED", VEM, "The VE manager did not accept the call.",
       "Sign in again; if it keeps happening, contact your platform admin.", 401),
    _e(9607, "VEM_UNAVAILABLE", VEM, "Something the VE manager needs isn't answering right now.",
       "Try again in a moment.", 503),
    _e(9608, "VEM_BAD_GATEWAY", VEM, "An endpoint answered the VE manager with something unexpected.",
       "Try again; if it keeps happening, restart the VE's endpoints.", 502),
    _e(9609, "VEM_ERROR", VEM, "An unexpected error in the VE manager.",
       "Try again. If it keeps happening, contact support with the time it happened.", 500),
    _e(9610, "VEM_REQUEST_INVALID", VEM, "The request has missing or wrongly typed fields.",
       "Check the fields listed with the error.", 422),
    # choreographies (inside the VEM)
    _e(9611, "CHOREOGRAPHY_NOT_FOUND", VEM, "There's no choreography with that name or id in this VE.",
       "Check the name; it may have been deleted.", 404),
    _e(9612, "CHOREOGRAPHY_EXISTS", VEM, "A choreography with that name already exists in this VE.",
       "Pick another name, or edit the existing one (that makes a new version).", 409),
    _e(9613, "CHOREOGRAPHY_INVALID", VEM, "The choreography's steps or connections aren't valid.",
       "Read the message: it names the step or connection to fix.", 422),
    _e(9614, "CHOREOGRAPHY_VERSION_NOT_FOUND", VEM, "That version of the choreography doesn't exist.",
       "Open the choreography's versions to see which ones exist.", 404),
    _e(9615, "CHOREOGRAPHY_RUN_NOT_FOUND", VEM, "There's no run with that id.",
       "Check the run id; old runs are hidden after the plan's retention.", 404),
    _e(9616, "CHOREOGRAPHY_RUN_NOT_ACTIVE", VEM, "The run already ended.",
       "Only a waiting or running run can be cancelled.", 409),
    _e(9617, "CHOREOGRAPHY_RUN_NOT_RESUMABLE", VEM, "This run can't be resumed.",
       "Only an ended run with failed, skipped or cancelled steps can be resumed.", 409),
    _e(9618, "CHOREOGRAPHY_QUEUE_FULL", VEM, "Too many runs of this choreography are waiting already.",
       "Wait for some to finish, or raise the choreography's parallel runs or queue limit.", 429),
    _e(9619, "CHOREOGRAPHY_TRIGGER_NOT_FOUND", VEM, "The choreography has no trigger with that id.",
       "Refresh the choreography's triggers.", 404),
    _e(9620, "CHOREOGRAPHY_STEP_PARAM_MISSING", VEM, "A step needs a parameter that nothing gave it.",
       "Set the parameter on the step, give it a default in the code, or make a previous step return it.", 422),
    _e(9621, "CHOREOGRAPHY_STEP_PARAM_AMBIGUOUS", VEM, "Previous steps sent different values for a parameter that must agree.",
       "Pick another merge rule for that parameter on the step (priority, from one step, ...).", 422),
    _e(9622, "CHOREOGRAPHY_STEP_TIMEOUT", VEM, "A step took longer than its time limit.",
       "Raise the step's time limit, or look at why the function or method is slow.", 504),
    _e(9623, "CHOREOGRAPHY_STEP_FAILED", VEM, "A step failed (after its retries).",
       "Open the step in the run to see its error and its job.", 409),
    _e(9624, "CHOREOGRAPHY_STEP_PARAM_MERGE_FAILED", VEM, "A parameter's values can't be combined the way the "
       "step says (for example a sum of texts).",
       "Pick another way to combine them, or another merge rule for that parameter.", 422),
    _e(9625, "JOB_RESULT_NOT_KEPT", VEM, "The job's result isn't kept: the job didn't succeed, hasn't "
       "finished, or its result was deleted when its keep time ended.",
       "Open the job to see its status; run it again with a longer \"Keep result for\" if you need the result.", 404),
    _e(9626, "JOB_NOT_ACTIVE", VEM, "The job already finished, so there's nothing to cancel.",
       "Refresh the job to see how it ended.", 409),
    _e(9627, "JOB_NOT_RETRYABLE", VEM, "This job can't be tried again: it's still going, it succeeded, "
       "or it's a step of a choreography run or an object call.",
       "Use Run again for a new job, or Resume on the choreography run.", 409),
    _e(9628, "FUNCTION_STYLE_UNSUPPORTED", VEM, "The function is written as f(params, ctx), a style that is no "
       "longer supported.",
       "Write it with named parameters, for example def resize(width: int, height: int = 100, ctx=None).", 422),
    _e(9629, "ENDPOINT_LOST", VEM, "The endpoint running the job stopped or could not be reached before it "
       "finished. This is the platform's failure: it is not billed and the job is tried again on another "
       "endpoint, up to 3 tries in all.",
       "Nothing to do; if it keeps happening, check the VE's endpoints.", 503),
    _e(9630, "RUNNER_SIZE_INVALID", VEM, "The runner size (CPU / memory) of a container function is bigger "
       "than the plan allows or than fits in one of the VE's endpoints.",
       "Pick a smaller size, or move the VE to a bigger plan / endpoint size.", 422),
    _e(9631, "RESULT_NOT_VISIBLE", VEM, "This job or run was started by someone else, and its \"Who sees runs "
       "and results\" setting doesn't share it with you.",
       "Ask who started it, or ask its creator or a VE Admin to share its runs with the VE or one of your teams.",
       403),
    _e(9632, "RESULT_VISIBILITY_FORBIDDEN", VEM, "Only who created it, or the VE's Admins and Owner, change who "
       "sees its runs and results.", "Ask its creator or a VE Admin.", 403),
    _e(9633, "EVENT_TYPE_INVALID", VEM, "That isn't an event type name you can use: lowercase letters, numbers, "
       "- and _, in parts joined by dots (e.g. order.paid). Names starting with file., schedule., run. or axo. "
       "are the platform's.", "Pick another name, like shop.order-paid.", 422),
    _e(9634, "EVENT_TYPE_IN_USE", VEM, "A choreography's newest version starts on this event type.",
       "Remove those triggers first (or save a version without them), then delete the type.", 409),
    _e(9635, "EVENT_TYPE_NOT_FOUND", VEM, "The VE has no event type with that name.",
       "Check the name on the Events page.", 404),
    _e(9636, "EVENT_NOT_FOUND", VEM, "There's no such event, or it isn't one you can see.",
       "Only VE Admins and Owners see every event; others see the ones they sent and the ones that started "
       "their triggers.", 404),
    _e(9637, "CHOREOGRAPHY_IN_USE", VEM, "Another choreography's newest version calls this one as a step.",
       "Remove that step from the other choreography (save a version without it), then delete this one.", 409),
    _e(9638, "CHOREOGRAPHY_LOOP", VEM, "Choreographies would call each other in a circle (A calls B, B calls A), "
       "so a run would never end.", "Remove one of the calls; the message names the choreographies in the circle.",
       422),
    _e(9639, "CHOREOGRAPHY_TOO_DEEP", VEM, "Choreographies calling choreographies go more than 5 levels deep.",
       "Call fewer levels: copy a small choreography's steps in, or join two levels into one.", 422),
    _e(9640, "CHOREOGRAPHY_TOO_BUSY", VEM, "The choreography this step calls had no free run slot before the "
       "step's \"Wait for a free slot\" time ran out.",
       "Raise the step's wait time, or the called choreography's runs at the same time / runs that may wait.", 429),
    _e(9641, "CHOREOGRAPHY_CALL_FAILED", VEM, "The run of the choreography this step calls failed.",
       "Open that run (linked from the step) to see which of its steps failed and why.", 409),
    # ── the web UI (99xx) ────────────────────────────────────────────────────
    _e(9901, "NETWORK_ERROR", UI, "The app could not reach the platform.",
       "Check your internet connection; if it's fine, the platform may be restarting -- try again in a minute."),
    _e(9902, "UNKNOWN_ERROR", UI, "The platform answered with something the app didn't expect.",
       "Reload the page. If it keeps happening, contact support with the time it happened."),
]

BY_NAME: Dict[str, ErrorInfo] = {e.name: e for e in CATALOG}
BY_CODE: Dict[int, ErrorInfo] = {e.code: e for e in CATALOG}


def lookup(name: str) -> Optional[ErrorInfo]:
    return BY_NAME.get(name)


def code_of(name: str, default: int = 0) -> int:
    info = BY_NAME.get(name)
    return info.code if info is not None else default


def problems() -> List[str]:
    """What's wrong with the catalog itself (tests assert it's empty)."""
    out = []
    if len(BY_NAME) != len(CATALOG):
        out.append("two entries share a name")
    if len(BY_CODE) != len(CATALOG):
        out.append("two entries share a code")
    for e in CATALOG:
        if not e.description.strip() or not e.hint.strip():
            out.append(f"{e.code} {e.name} has no description or hint")
        if e.component not in COMPONENTS:
            out.append(f"{e.code} {e.name} has an unknown component {e.component!r}")
    return out


def as_json() -> Dict[str, object]:
    """What ``GET /errors`` returns."""
    return {"components": COMPONENTS, "errors": [e.to_dict() for e in sorted(CATALOG, key=lambda e: e.code)]}
