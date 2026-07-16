"""Where finished exports land on the local filesystem."""

EXPORT_OUTPUT_DIR = "/var/data/exports"


def build_export_path(export_name):
    return f"{EXPORT_OUTPUT_DIR}/{export_name}.csv"
