# Independent metric verification for PR #1974

The canonical result and full evidence limits are in [../README.md](../README.md)
and `../report.json`. This helper uses independent metric arithmetic with the
same strict input identity loader and report schema; it is not an independent
review of measurement provenance.

```sh
python3 bench/results/2026-10-09_jev_baseline/verify/score_independent.py
```

Inputs are committed public/critical suites, ungated predictions, historical
pipeline telemetry, and all three saved Llama runs. Public106 metadata and
critical2 metadata are separate. Expected suite hashes are checked, and every
run's IDs/counts/warmup/gold labels are checked. Public output uses relative
references/content hashes and has no resolved local paths.

The matched103 comparison retains Jev69/103 versus Llama66/103 per run, and
live-log44/75 on both sides. Llama notification FP is6/309 on matched attempts
and6/315 on its original105-case runs. Missing predictions stay in denominators.

Historical pipeline telemetry has no case IDs or published batch mapping.
The helper records `pipeline_reproduction.state=unavailable` and its raw digest;
it does not assign identities by position or regenerate pipeline case scores.
Prior pipeline numbers remain historical and unverified, not a production
acceptance or actual TTS measurement. No new provider experiment was run.

`bench/tests/test_jev_baseline.py` checks arithmetic parity, miss denominators,
metadata totals, input rejection, byte reproduction across checkout locations,
and runtime ID capture/retry retention with a synthetic classifier.
