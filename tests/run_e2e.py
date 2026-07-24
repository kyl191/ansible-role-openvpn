#!/usr/bin/env python3
"""Thin CLI entry point - the actual implementation lives in tests/e2e/, split
by concern (aws, ssh, provisioning, verification, terraform, report, display,
orchestrator). See tests/e2e/orchestrator.py:main for the top-level flow."""

from e2e.orchestrator import main

if __name__ == "__main__":
    main()
