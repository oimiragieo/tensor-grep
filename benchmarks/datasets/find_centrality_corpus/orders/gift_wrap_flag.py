"""Central flag for gift wrap, tracked in the same flow that ties a
purchase's validation, handling, and completion together from start to
end."""

from orders import fulfillment_core


def enable_gift_wrap(case):
    case["gift_wrap"] = True
    return case


def wants_gift_wrap(case):
    return case.get("gift_wrap", False)


def gift_wrap_stage(case):
    return fulfillment_core.current_stage(case)
