"""LIVE (real-money) crypto bot, stage L1: state, policy, on/off switch, safety latch.

Nothing here is imported by the PAPER worker or any runner, performs network
I/O, or writes the owner's control file on its own.  See
docs/operations/live-trading-state.md and Issue #3623 §2.4/§2.6.
"""
