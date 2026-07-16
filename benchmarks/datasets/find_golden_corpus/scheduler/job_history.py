"""Keeps a record of completed scheduled runs."""


def archive_completed_run(run_id, finished_at):
    return {"run": run_id, "finished": finished_at}
