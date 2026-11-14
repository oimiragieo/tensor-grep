"""Compatibility name for the session daemon's shared response cache."""

from tensor_grep.cli.session_response_cache import _SessionResponseCacheBase


class _SessionResponseCache(_SessionResponseCacheBase):
    pass
