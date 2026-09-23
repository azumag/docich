"""A delayed corner restore may never consume another runtime's identity."""
import uuid

from test_coordinator import CoordinatorTestBase
from docich import game_switch


class TestExpectedSource(CoordinatorTestBase):
    def test_each_identity_field_is_required_before_any_adapter_call(self):
        self.coordinator.start('hanjuku')
        active = self.canonical()['active']
        identity = {k: active[k] for k in ('game', 'runtime_id', 'generation', 'lease_id')}
        before = self.canonical()
        events = {k: list(v.runtime.events) for k, v in self.factory.adapters.items()}
        for field, value in [('game', 'robots'), ('runtime_id', 'g99-deadbeef'),
                             ('generation', 99), ('lease_id', str(uuid.uuid4()))]:
            for stop in (True, False):
                with self.subTest(field=field, stop=stop):
                    payload = {'expected_source': {**identity, field: value}}
                    result = (self.coordinator.stop(payload=payload) if stop else
                              self.coordinator.switch('robots', payload=payload))
                    self.assertEqual(result.error_code, game_switch.ERROR_SOURCE_FENCE_LOST)
                    self.assertEqual(self.canonical()['active'], before['active'])
                    self.assertEqual(self.canonical()['phase'], 'ready')
                    self.assertEqual({k: v.runtime.events for k, v in self.factory.adapters.items()}, events)
        for malformed in ({}, None, {'game': 'hanjuku'}):
            result = self.coordinator.stop(payload={'expected_source': malformed})
            self.assertEqual(result.error_code, game_switch.ERROR_SOURCE_FENCE_LOST)

    def test_matching_stop_and_restore_keep_request_idempotency(self):
        for target in (None, 'robots'):
            with self.subTest(target=target):
                if self.canonical().get('active'):
                    self.coordinator.stop()
                self.coordinator.start('hanjuku')
                active = self.canonical()['active']
                payload = {'expected_source': {k: active[k] for k in
                           ('game', 'runtime_id', 'generation', 'lease_id')}}
                request_id = str(uuid.uuid4())
                invoke = lambda: (self.coordinator.stop(request_id=request_id, payload=payload)
                                  if target is None else self.coordinator.switch(
                                      target, request_id=request_id, payload=payload))
                self.assertEqual(invoke().status, 'succeeded')
                state = self.canonical()
                self.assertEqual(invoke().status, 'succeeded')
                self.assertEqual(self.canonical(), state)

    def test_queued_stale_source_is_failed_durably_and_conflicts_stay_conflicts(self):
        self.coordinator.start('hanjuku')
        active = self.canonical()['active']
        identity = {k: active[k] for k in ('game', 'runtime_id', 'generation', 'lease_id')}
        request_id = str(uuid.uuid4())
        payload = {'expected_source': identity}
        with self.store.transaction() as tx:
            tx.enqueue_request(request_id, 'switch', 'robots', payload)
        changed = self.canonical()
        changed['active']['lease_id'] = str(uuid.uuid4())
        self.store.canonical.save(changed)
        result = self.coordinator.switch('robots', request_id=request_id, payload=payload)
        self.assertEqual(result.error_code, game_switch.ERROR_SOURCE_FENCE_LOST)
        self.assertEqual(self.store.receipts.load(request_id)['status'], 'failed')
        self.assertFalse(self.store.receipts.queued())
        self.assertEqual(self.coordinator.switch('robots', request_id=request_id, payload=payload), result)
        conflict = self.coordinator.switch('robots', request_id=request_id,
                    payload={'expected_source': {**identity, 'generation': 99}})
        self.assertEqual(conflict.error_code, game_switch.ERROR_REQUEST_CONFLICT)
        self.assertEqual(self.canonical()['active'], changed['active'])
