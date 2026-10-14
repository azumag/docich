# Queue giveup attribution

The production runtime monitor receives two different views of AI queue giveups:

- `queues.queue_giveups_15m`: the full parsed 15-minute aggregate from the VM collector.
- `ai.recent_events`: a bounded redacted event sample. Older queue giveups may be absent when other failure/winner events fill the sample.

The public monitor must not infer a component from the aggregate alone. `summarize_runtime_queue_attribution.py` therefore preserves the existing runtime summary and adds a full-window fixed-category projection:

- `ai_queue_giveup_component_radio_prepass`
- `ai_queue_giveup_component_radio_main`
- `ai_queue_giveup_component_news_spam_check`
- `ai_queue_giveup_component_comment`
- `ai_queue_giveup_component_improvement`
- `ai_queue_giveup_component_other`
- `ai_queue_giveup_component_unknown`
- `ai_queue_giveup_attribution_consistent`
- `ai_queue_giveup_attribution_exact`

Known component counts come only from queue-giveup events that remain in the redacted recent sample. The difference between the full 15-minute aggregate and the sampled queue-giveup count is assigned to `unknown`. This makes the fixed buckets sum to the aggregate without pretending that an event dropped by the bounded sample belonged to a particular lane/component.

`attribution_exact=1` means all aggregate queue giveups are still represented in the recent sample. `attribution_consistent=0` means the payload is internally inconsistent (for example the sample contains more giveups than the aggregate) or the recent sample is unavailable; in that case the helper fails closed by assigning the aggregate to `unknown` and publishes no partial known-component attribution.

Dynamic labels, provider/model identities, error text, paths, prompts and credentials are never added to the public summary. The existing `ai_recent_queue_giveup_*` metrics remain available as explicit bounded-sample metrics for backward compatibility.
