"""Reports whether the process can currently serve traffic."""


def liveness_summary(dependency_checks):
    return {"ok": all(check() for check in dependency_checks)}
