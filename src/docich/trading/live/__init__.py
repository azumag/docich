"""LIVE (real-money) crypto bot, stage L1: state, policy, enable gate, safety latch.

Nothing here is imported by the PAPER worker or any runner, performs network
I/O, or creates the owner-only enable file.  See docs/operations/live-trading-state.md
and Issue #3623 §2.4/§2.6.
"""
