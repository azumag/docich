"""手順層: multi-step UI procedures that carry out a plan or judgment.

Each flow reads the current screen, asks ``strategy``/``judgment`` what is
wanted and uses ``os_layer`` commands to press toward it, holding or
cancelling when the visible screen contradicts the plan.
"""
