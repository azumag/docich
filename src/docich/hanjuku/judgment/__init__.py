"""判断層: situational judgment (battle tactics, survival, event answers).

Judgment evaluates the observed situation against the strategy's plan and
returns a decision. It never reads the screen and never produces pad input.
It may use ``os_layer`` facts and ``strategy`` plans but must not import
``flows`` or ``orchestrator``.
"""
