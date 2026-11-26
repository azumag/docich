"""Split-timer records for the hanjuku-speedrun corner.

The package is deliberately small and independent of the generated
``docich.hanjuku_speedrun`` copy:

* :mod:`timer` is a pure state machine (no file or clock access),
* :mod:`store` owns atomic files, bounds and corrupt-file recovery,
* :mod:`hook` is the only entry point the bot calls,
* :mod:`trace` writes the bounded observation trace and periodic frames,
* :mod:`spool` collects rotated logs, compresses them off the hot path and
  keeps the per-attempt spool under a byte cap,
* :mod:`report_segments` and :mod:`manifest` write the attempt summary,
* :mod:`archive` uploads finished attempts to R2 (unconfigured means no send),
* :mod:`preview` serves the real broadcast overlay locally for a trial.

Nothing here talks to the network or changes bot input; compression is
detached and never awaited.
"""
