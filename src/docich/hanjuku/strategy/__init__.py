"""戦略層: what to do next (chart orders, sortie kits, replanning, monthly budget).

Strategy reads the world model and decision records and returns plans; it
never reads the screen and never produces pad input. It may use ``os_layer``
facts but must not import ``judgment``, ``flows`` or ``orchestrator``.
"""
