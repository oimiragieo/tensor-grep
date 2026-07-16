"""What to show a visitor whose preference we cannot honor."""

DEFAULT_REGION_TAG = "en-US"


def choose_or_default(requested_code, supported_codes):
    return requested_code if requested_code in supported_codes else DEFAULT_REGION_TAG
