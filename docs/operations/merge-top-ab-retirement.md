# Merge-top A/B launcher retirement

The one-off merge-top experiment compares the same strategy with
`ANALYZE_BOARD_MERGE_TOP_MODEL=2` (A) versus `=1` (B). Its dedicated
`soren-merge-top-ab-probe.yml` workflow has been removed.

## Why

The launcher preserved an identical active experiment, but did not consult
completed experiments. After completion, a later eligible Soren deployment
could start the same comparison again, reset an adopted mode 1 to mode 2,
or finish an unrelated A/B before replacing it with this experiment.

## Runtime behavior

- Removing the launcher does **not** finish, restart, or reset the active A/B.
- The existing VM A/B controller continues collecting matches and applying its
  configured verdict. Winner promotion is unchanged.
- Normal deployment and the strategy improvement loop are unchanged. Later A/B
  experiments are not forced to compare merge-top settings by this launcher.
- Do not run the retired workflow to verify retirement. Before merging, check
  that no queued/in-progress invocation remains; cancel only this launcher's
  outstanding runs if present. After deployment, verify the active experiment's
  identity, continued match collection, and the encoder PID.

Any repeat of this experiment requires a new explicit operator request and a
reviewed operation. It must preserve the current winner as baseline, refuse to
replace an unrelated active experiment, and must not be coupled to deployments.
