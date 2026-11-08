# docich-stream.service (placed, not installed)

ADR 0007 stage D. **This directory only ships the unit and its wrapper.** Nothing
installs, enables or starts it; the legacy runner (`soren-runtime.service` →
`start_all.sh` → `direct_stream.sh`) remains the encoder owner until the explicit
cutover (stage E, requires approval and a time window).

* `bin/docich-stream-run` loads the runtime `.env` with shell semantics (not
  `EnvironmentFile`) and execs `python3 -m docich.stream_supervisor run`.
* `DOCICH_STREAM_OWNER` (`soren` default / `docich`) decides which side starts the
  encoder. With `soren` the supervisor exits 75 (success, not restarted) without doing anything.
* The supervisor never starts PulseAudio: it waits for the user-manager server and
  creates the null sink only when missing. The unit must not own Pulse (a
  `KillMode=control-group` restart would take it down; docich#2008).
* `tmp/state/docich_stream.paused` is the operator pause marker (WebUI stop/start). While it exists the
  supervisor idles without an encoder or a lock; `Restart=always` respawns it after a clean stop.
* Stop budget: q 15 s + SIGINT 15 s < `TimeoutStopSec=45`; `KillMode=mixed`.

See `docs/adr/0007-direct-stream-single-owner-supervisor.md` §5 for the cutover
and rollback procedure.
