"""Structured private records. Never serialize exceptions or credentials."""

import json

from .local_files import write_private


def response(status, data=None, messages=()):
    return {"status": status, "data": data or {}, "messages": list(messages)}


def save_record(path, value):
    write_private(path, json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2).encode("utf-8"), replace=True)
