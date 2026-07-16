"""Tamper-evident logging for privileged operator actions."""

import time


def record_privileged_action(actor_id, action_name, target_ref):
    entry = {"actor": actor_id, "action": action_name, "target": target_ref, "ts": time.time()}
    _LEDGER.append(entry)
    return entry


_LEDGER = []


def export_ledger():
    return list(_LEDGER)
