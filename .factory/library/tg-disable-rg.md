# TG_DISABLE_RG Environment Variable

The `TG_DISABLE_RG` environment variable is a hidden/internal testing flag used to disable the ripgrep fallback behavior. 

When `TG_DISABLE_RG` is set (e.g., to "1"), `tensor-grep` acts as if `rg` is unavailable on the `PATH`, forcing the native CPU engine to handle the search request even when `rg` is installed. This is useful for testing fallback mechanisms and routing logic.

*Source: Identified during review of feature `wire-native-engine-routing` in the `native-cpu-engine` milestone.*
