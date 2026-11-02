"""Layered deterministic Hanjuku Hero policy (no model, provider or network).

One observation flows top to bottom::

    orchestrator   decide(): which owner handles this frame
        |
    flows          手順層: multi-step menu procedures (deploy, month, battle menus, ...)
        |
    judgment       判断層: tactical judgment (card gates, survival, eggs, events)
        |
    strategy       戦略層: chart orders, sortie kits, replanning, monthly budget
        |
    os_layer       OS層: screen understanding (phase/readers/world) and
                   command execution (commands/navigation)

    decision_log   structured decision records, usable from every layer

A layer may import only the layers below it. ``tests/test_hanjuku_layers.py``
enforces the direction. The legacy modules ``docich.hanjuku_policy`` and
``docich.hanjuku_bot`` remain as compatibility facades over this package; new
code should import the layer module that owns a name.

Design notes and the migration plan: ``docs/hanjuku_layered_architecture.md``.
"""
