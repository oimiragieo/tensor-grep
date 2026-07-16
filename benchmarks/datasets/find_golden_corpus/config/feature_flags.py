"""Per-account capability gating."""

_ENABLED_CAPABILITIES = {"beta_exports", "bulk_invite"}


def is_capability_enabled(capability_name, account_tier):
    if account_tier == "internal":
        return True
    return capability_name in _ENABLED_CAPABILITIES
