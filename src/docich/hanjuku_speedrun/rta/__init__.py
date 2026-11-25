"""Split-timer records for the hanjuku-speedrun corner.

The package is deliberately small and independent of the generated
``docich.hanjuku_speedrun`` copy:

* :mod:`timer` is a pure state machine (no file or clock access),
* :mod:`store` owns atomic files, bounds and corrupt-file recovery,
* :mod:`hook` is the only entry point the bot calls.

Nothing here talks to the network, starts a process, or changes bot input.
"""
