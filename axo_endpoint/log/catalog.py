from __future__ import annotations


class Component:
    APP                              = "app"
    HEARTBEAT                        = "heartbeat"
    ROUTER                           = "router"
    DISPATCHER                       = "dispatcher"
    RUNTIME                          = "runtime"
    HANDLER_JOB_SUBMIT               = "handler.job_submit"
    USAGE                            = "core.usage"
    HANDLER_JOB_RESULT               = "handler.job_result"
    HANDLER_FUNCTION_REGISTER        = "handler.function_register"
    HANDLER_FUNCTION_UPDATE          = "handler.function_update"
    HANDLER_FUNCTION_DELETE          = "handler.function_delete"
    HANDLER_PEER_ANNOUNCE            = "handler.peer_announce"
    HANDLER_CONTAINER_BOOTSTRAP      = "handler.container_bootstrap"
    CONTAINER_RUNTIME                = "container_runtime"
    CONTAINER_SPAWNER                = "container_spawner"
    CONTAINER_RUNNER                 = "container_runner"
    RESULT_RECEIVER                  = "result_receiver"
    CONSENSUS                        = "consensus"
    HANDLER_STATE_SYNC_PUSH          = "handler.state_sync_push"
    HANDLER_STATE_SYNC_PULL          = "handler.state_sync_pull"
    HANDLER_LEADER_PROXY             = "handler.leader_proxy"
    DATAIO                           = "dataio"
    HANDLER_ACTIVITY_LIST            = "handler.activity_list"
    EXTERNAL_FORWARDING              = "external_forwarding"
    CONCURRENCY_LEDGER               = "concurrency_ledger"
    HANDLER_CONCURRENCY_SLOT_REQUEST = "handler.concurrency_slot_request"
    HANDLER_CONCURRENCY_SLOT_RELEASE = "handler.concurrency_slot_release"
    HANDLER_CONCURRENCY_RECONCILE_PULL = "handler.concurrency_reconcile_pull"
    HANDLER_JOB_FORWARD              = "handler.job_forward"
    HANDLER_JOB_RESULT_SYNC          = "handler.job_result_sync"
    HANDLER_CONSISTENCY_CHECK_REQUEST = "handler.consistency_check_request"
    RESULT_CONSISTENCY               = "result_consistency"
    HANDLER_JOB_CANCEL               = "handler.job_cancel"


class Event:
    class App:
        DRAIN_REQUESTED = "APP.DRAIN_REQUESTED"
        STARTED   = "APP.STARTED"
        STOPPED   = "APP.STOPPED"
        HEARTBEAT = "APP.HEARTBEAT"

    class Heartbeat:
        SUBSCRIBER_STARTED = "HEARTBEAT.SUBSCRIBER_STARTED"
        SUBSCRIBER_STOPPED = "HEARTBEAT.SUBSCRIBER_STOPPED"
        FRAME_DROPPED      = "HEARTBEAT.FRAME_DROPPED"
        DECODE_ERROR       = "HEARTBEAT.DECODE_ERROR"
        FOREIGN_VE_DROPPED = "HEARTBEAT.FOREIGN_VE_DROPPED"

    class Peer:
        DISCOVERED        = "PEER.DISCOVERED"
        HEARTBEAT         = "PEER.HEARTBEAT"
        EVICTED           = "PEER.EVICTED"
        ANNOUNCE_SENT     = "PEER.ANNOUNCE_SENT"
        ANNOUNCE_RECEIVED = "PEER.ANNOUNCE_RECEIVED"
        ANNOUNCE_REJECTED = "PEER.ANNOUNCE_REJECTED"

    class Router:
        REQUEST_DROPPED    = "ROUTER.REQUEST_DROPPED"
        REQUEST_RECEIVED   = "ROUTER.REQUEST_RECEIVED"
        REQUEST_HANDLED    = "ROUTER.REQUEST_HANDLED"
        REQUEST_DISPATCHED = "ROUTER.REQUEST_DISPATCHED"
        PUSH_SKIPPED       = "ROUTER.PUSH_SKIPPED"
        PUSH_SENT          = "ROUTER.PUSH_SENT"

    class Dispatcher:
        CLOSED            = "DISPATCHER.CLOSED"
        UNKNOWN_OPERATION = "DISPATCHER.UNKNOWN_OPERATION"
        QUEUE_FULL        = "DISPATCHER.QUEUE_FULL"
        HANDLER_EXC       = "DISPATCHER.HANDLER_EXC"

    class Runtime:
        JOB_INVOKED    = "RUNTIME.JOB_INVOKED"
        WORKER_SPAWNED = "RUNTIME.WORKER_SPAWNED"
        WORKER_REAPED  = "RUNTIME.WORKER_REAPED"
        WORKER_CRASHED = "RUNTIME.WORKER_CRASHED"
        WORKER_JOB_TIMEOUT = "RUNTIME.WORKER_JOB_TIMEOUT"
        WORKER_CANCELLED = "RUNTIME.WORKER_CANCELLED"

    class Job:
        SUBMITTED    = "JOB.SUBMITTED"
        COMPLETED    = "JOB.COMPLETED"
        FAILED       = "JOB.FAILED"
        RESULT_POLLED = "JOB.RESULT_POLLED"
        RETRY_SCHEDULED = "JOB.RETRY_SCHEDULED"
        RETRY_EXHAUSTED = "JOB.RETRY_EXHAUSTED"
        CANCEL_REQUESTED = "JOB.CANCEL_REQUESTED"

    class Usage:
        RECORDED = "USAGE.RECORDED"

    class Function:
        REGISTERED = "FUNCTION.REGISTERED"
        UPDATED    = "FUNCTION.UPDATED"
        DELETED    = "FUNCTION.DELETED"

    class DataIO:
        REQUEST_RECEIVED = "DATAIO.REQUEST_RECEIVED"
        REQUEST_RESOLVED = "DATAIO.REQUEST_RESOLVED"
        REQUEST_FAILED   = "DATAIO.REQUEST_FAILED"

    class Consensus:
        TICK                   = "CONSENSUS.TICK"
        LEADER_ELECTED         = "CONSENSUS.LEADER_ELECTED"
        STEPPED_DOWN           = "CONSENSUS.STEPPED_DOWN"
        REPLICATION_FLUSHED    = "CONSENSUS.REPLICATION_FLUSHED"
        SYNC_PUSH_RECEIVED     = "CONSENSUS.SYNC_PUSH_RECEIVED"
        SYNC_PUSH_REJECTED     = "CONSENSUS.SYNC_PUSH_REJECTED"
        SYNC_PULL_SERVED       = "CONSENSUS.SYNC_PULL_SERVED"
        REQUEST_FORWARDED      = "CONSENSUS.REQUEST_FORWARDED"
        REQUEST_FORWARD_FAILED = "CONSENSUS.REQUEST_FORWARD_FAILED"
        QUORUM_LOST            = "CONSENSUS.QUORUM_LOST"
        QUORUM_RESTORED        = "CONSENSUS.QUORUM_RESTORED"
        DEGRADED               = "CONSENSUS.DEGRADED"

    class Activity:
        LISTED = "ACTIVITY.LISTED"

    class External:
        PUBLISHED             = "EXTERNAL.PUBLISHED"
        PUBLISH_FAILED        = "EXTERNAL.PUBLISH_FAILED"
        DELIVERY_RETRY        = "EXTERNAL.DELIVERY_RETRY"
        VERSION_UNKNOWN_SKIPPED = "EXTERNAL.VERSION_UNKNOWN_SKIPPED"

    class Container:
        SPAWNED             = "CONTAINER.SPAWNED"
        READY               = "CONTAINER.READY"
        DISMISSED           = "CONTAINER.DISMISSED"
        CRASHED             = "CONTAINER.CRASHED"
        JOB_TIMEOUT         = "CONTAINER.JOB_TIMEOUT"
        JOB_CANCELLED       = "CONTAINER.JOB_CANCELLED"
        BOOTSTRAP_REQUESTED = "CONTAINER.BOOTSTRAP_REQUESTED"
        BOOTSTRAP_COMPLETE  = "CONTAINER.BOOTSTRAP_COMPLETE"
        JOB_DISPATCHED      = "CONTAINER.JOB_DISPATCHED"
        JOB_RECEIVED        = "CONTAINER.JOB_RECEIVED"
        RESULT_PUSHED       = "CONTAINER.RESULT_PUSHED"
        BUILD_STARTED       = "CONTAINER.BUILD_STARTED"
        BUILD_COMPLETE      = "CONTAINER.BUILD_COMPLETE"
        WAITING_FOR_ROOM    = "CONTAINER.WAITING_FOR_ROOM"

    class Concurrency:
        SLOT_GRANTED            = "CONCURRENCY.SLOT_GRANTED"
        SLOT_AT_CAPACITY        = "CONCURRENCY.SLOT_AT_CAPACITY"
        SLOT_RELEASED           = "CONCURRENCY.SLOT_RELEASED"
        JOB_FORWARDED           = "CONCURRENCY.JOB_FORWARDED"
        RECONCILIATION_STARTED  = "CONCURRENCY.RECONCILIATION_STARTED"
        RECONCILIATION_FINISHED = "CONCURRENCY.RECONCILIATION_FINISHED"

    class ResultConsistency:
        REPLICATION_STARTED   = "RESULT_CONSISTENCY.REPLICATION_STARTED"
        REPLICATION_SUCCEEDED = "RESULT_CONSISTENCY.REPLICATION_SUCCEEDED"
        REPLICATION_FAILED    = "RESULT_CONSISTENCY.REPLICATION_FAILED"
        FANOUT_FAILED         = "RESULT_CONSISTENCY.FANOUT_FAILED"
        RESULT_RECEIVED       = "RESULT_CONSISTENCY.RESULT_RECEIVED"
        HASH_MISMATCH         = "RESULT_CONSISTENCY.HASH_MISMATCH"
        CHECK_STARTED         = "RESULT_CONSISTENCY.CHECK_STARTED"
        CHECK_FINISHED        = "RESULT_CONSISTENCY.CHECK_FINISHED"
