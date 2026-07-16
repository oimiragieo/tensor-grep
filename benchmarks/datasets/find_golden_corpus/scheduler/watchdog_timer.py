"""Notices a recurring run that stopped reporting results."""


def flag_silent_job(job_name, last_result_epoch, now_epoch, quiet_ceiling_s):
    if now_epoch - last_result_epoch > quiet_ceiling_s:
        return {"job": job_name, "silent_for": now_epoch - last_result_epoch}
    return None
