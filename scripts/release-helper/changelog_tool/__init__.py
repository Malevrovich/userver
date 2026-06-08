"""CHANGELOG preparation tool for userver releases.

This tool helps maintainers prepare a CHANGELOG from git history between
two releases. It collects commits, identifies external contributors, filters
uninteresting commits via heuristics, sends remaining commits to an LLM for
classification, and generates a reviewable report with a proposed CHANGELOG.

The tool does not fully automate the release process — it only assists with
data collection, classification, and verification.
"""

from __future__ import annotations

__version__ = "0.1.0"
