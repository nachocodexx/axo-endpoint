from __future__ import annotations


class Component:
    SERVER       = "server"
    HTTP         = "http"
    AUTH         = "auth"
    INTAKE       = "intake"
    PROVISIONER  = "provisioner"
    SLEEP        = "sleep"
    CLEANUP      = "cleanup"
    XOLO         = "xolo"
    STORAGE      = "storage"
    FRONT_DOOR   = "front_door"
    BOOTSTRAP    = "bootstrap"


class Event:
    class Server:
        STARTED = "SERVER.STARTED"
        STOPPED = "SERVER.STOPPED"
        LOOP_FAILED = "SERVER.LOOP_FAILED"

    class Intake:
        STARTED            = "INTAKE.STARTED"
        FRAME_MALFORMED    = "INTAKE.FRAME_MALFORMED"
        ENVELOPE_MALFORMED = "INTAKE.ENVELOPE_MALFORMED"
        UNKNOWN_EVENT_TYPE = "INTAKE.UNKNOWN_EVENT_TYPE"
        FOREIGN_VE         = "INTAKE.FOREIGN_VE"
        SAVE_FAILED        = "INTAKE.SAVE_FAILED"
        DUPLICATE_SKIPPED  = "INTAKE.DUPLICATE_SKIPPED"

    class Provisioner:
        STEP          = "PROVISIONER.STEP"
        STEP_FAILED   = "PROVISIONER.STEP_FAILED"
        VE_READY      = "PROVISIONER.VE_READY"
        VE_DELETED    = "PROVISIONER.VE_DELETED"
        VEM_DOWN      = "PROVISIONER.VEM_DOWN"

    class Sleep:
        SLEEPING      = "SLEEP.SLEEPING"
        WAKING        = "SLEEP.WAKING"
        DELIVERED     = "SLEEP.DELIVERED"
        WAKE_FAILED   = "SLEEP.WAKE_FAILED"

    class Cleanup:
        SUSPECT       = "CLEANUP.SUSPECT"
        REMOVED       = "CLEANUP.REMOVED"
        REMOVE_FAILED = "CLEANUP.REMOVE_FAILED"
        SKIPPED_VE    = "CLEANUP.SKIPPED_VE"

    class Auth:
        UNAUTHORIZED  = "AUTH.UNAUTHORIZED"
        FORBIDDEN     = "AUTH.FORBIDDEN"
        XOLO_DOWN     = "AUTH.XOLO_DOWN"

    class Bootstrap:
        ADMIN_CREATED = "BOOTSTRAP.ADMIN_CREATED"
        ADMIN_EXISTS  = "BOOTSTRAP.ADMIN_EXISTS"
        FAILED        = "BOOTSTRAP.FAILED"
