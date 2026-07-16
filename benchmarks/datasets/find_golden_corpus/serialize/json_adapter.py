"""Adapts an internal object to the public wire shape."""

import json


def to_wire_format(obj):
    return json.dumps(obj, sort_keys=True)
