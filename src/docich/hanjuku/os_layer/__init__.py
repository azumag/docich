"""OS層: screen understanding and command execution for Hanjuku Hero.

``phase`` classifies the raw frame, ``readers`` extracts facts from a parsed
screen, ``world`` keeps the observed world model, ``commands`` builds pad
input toward a visible label and ``navigation`` moves the map cursor toward a
goal. Nothing here decides a goal: modules in this package must not import
``strategy``, ``judgment``, ``flows`` or ``orchestrator``.
"""
