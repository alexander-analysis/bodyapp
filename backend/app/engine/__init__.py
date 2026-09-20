"""Deterministic engine: pure functions, no I/O.

THE RULE: every number the user acts on comes from here or from a row, never
from the LLM. Gemini identifies food and writes prose; it never computes a
target, a rollup value or a guardrail. See CLAUDE.md.
"""
