from contextvars import ContextVar
from uuid import uuid4

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_operation_id_var: ContextVar[str] = ContextVar("operation_id", default="")


def set_request_id(request_id: str = "") -> str:
    rid = request_id or uuid4().hex[:12]
    _request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id_var.get()


def set_operation_id(operation_id: str) -> None:
    _operation_id_var.set(operation_id)


def get_operation_id() -> str:
    return _operation_id_var.get()


def clear_operation_id() -> None:
    _operation_id_var.set("")


class RequestLogFilter:
    def filter(self, record):
        rid = get_request_id()
        if rid:
            record.request_id = rid
        else:
            record.request_id = "-"
        oid = get_operation_id()
        if oid:
            record.operation_id = oid
        return True