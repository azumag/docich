from __future__ import annotations

import unittest
from types import SimpleNamespace

from docich.actions import Action
from docich.adapters.base import Observation
from docich.agent.brains import NethackPolicyBrain
from docich.nethack_policy import PolicyDecision


class TestNethackPolicyBrainDenylistWiring(unittest.TestCase):
    def test_brain_rejects_denylisted_policy_command(self) -> None:
        game = SimpleNamespace(
            name="nethack",
            adapter="cli",
            raw={"cli": {"cols": 80, "rows": 5}},
            agent=SimpleNamespace(brain="nethack", command=""),
        )
        brain = NethackPolicyBrain(SimpleNamespace(), game)
        brain.policy.decide = lambda _obs: PolicyDecision(
            layer="midlevel",
            intent="regression_probe",
            reason="exercise production denylist wiring",
            actions=(Action(type="text", text="Q"),),
        )
        obs = Observation(
            game="nethack",
            title="NetHack",
            adapter="cli",
            ts=1.0,
            kind="text",
            text=(
                "\n"
                ".@....\n"
                "......\n"
                "Dlvl:2 $:5 HP:10(10) Pw:4(4) AC:5 Exp:2\n"
                "T:12 \n"
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "denied command 'Q'"):
            brain.decide(obs)


if __name__ == "__main__":
    unittest.main()
