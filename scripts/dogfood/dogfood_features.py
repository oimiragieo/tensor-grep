#!/usr/bin/env python3
"""Compatibility entry point for the packaged feature dogfood runner."""

from tensor_grep.cli.dogfood_features import main

if __name__ == "__main__":
    raise SystemExit(main())
