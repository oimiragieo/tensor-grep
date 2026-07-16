"""Executes ready work once its planned moment is reached."""


def trigger_ready_tasks(job_table, now_epoch):
    ready = [job for job in job_table if job["run_at"] <= now_epoch]
    for job in ready:
        job["handler"]()
    return ready
