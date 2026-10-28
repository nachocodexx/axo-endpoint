from __future__ import annotations


class Component:
    SERVER  = "server"
    SERVICE = "service"
    ACCESS  = "access"


class Event:
    class Server:
        STARTED          = "SERVER.STARTED"
        STOPPED          = "SERVER.STOPPED"
        FRAME_MALFORMED  = "SERVER.FRAME_MALFORMED"
        HANDLER_CRASHED  = "SERVER.HANDLER_CRASHED"

    class Request:
        HANDLED   = "REQUEST.HANDLED"
        REJECTED  = "REQUEST.REJECTED"

    class Bucket:
        CREATED = "BUCKET.CREATED"
        DELETED = "BUCKET.DELETED"

    class Object:
        REGISTERED = "OBJECT.REGISTERED"
        COMPLETED  = "OBJECT.COMPLETED"
        FAILED     = "OBJECT.FAILED"
        DELETED    = "OBJECT.DELETED"
        WRITTEN_REPORT_FAILED = "OBJECT.WRITTEN_REPORT_FAILED"
