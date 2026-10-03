# TwiCa browser evidence / transport recovery

Refs #1236 / #1619. Screenshot liveness is not card delivery.

`twica_renderer.py` keeps the page and its queue alive after a failed events
request. The TwiCa application's existing HTTP/WS controller owns retries;
a temporary request failure must not trigger a competing full-page restart.
Browser death and repeated screenshot failures still use the existing bounded
service recovery. A failed HTTP response (including 401/403/429/5xx) is recorded
separately, not treated as a successful application response.

The existing common component gains private `browser-health.json` evidence,
without adding a new service or changing the legacy ownership registry. Its
writer PID/start identity and generation must match the fresh renderer and
current common policy. Fields are fixed enums, booleans and bounded counters:
page_state, events_state, config_state, socket_state, dom_state, card_present,
card_visible, images_ready, events_responses, config_responses, network_failures,
script_errors, socket_frames, cards_observed. Socket frames may be heartbeats;
DOM observations are not event IDs or final encoded pixels. No response body,
card text, username, URL, cookie, storage, or error message is exported.

`twica_browser_operator.py transport|dom|errors` reads only this private record
and current ownership/heartbeat records, returning fixed exit categories.
`refresh` reuses the existing common renderer service restart only; it verifies
common ownership and the same native encoder PID/adapter identity before and
after. It never invokes prepare/arm/activate/enable, changes policies, or
restarts games, Xvfb, the shared rail service, or the encoder.

The recovery workflow runs production only on a reviewed refresh-epoch push.
It waits for exact deployment without holding the VM mutation lock, then takes
the existing VM operation concurrency group and checks current main/deployed
SHA again. PR runs test only. There is no schedule or arbitrary command input.
The operator uses the existing gateway's withheld stdout; Actions exposes only
fixed categories. An observed empty overlay is not proof of a missing event.
A renderer refresh restores the existing private checkpoint but is not a
claim that an unobserved in-flight queue can be migrated exactly once.

Physical layer evidence remains available through `probe_twica_runtime.py`.
Actual TwiCa events across non-sorengame corners, audio and final stream frames
remain separate production acceptance checks.
