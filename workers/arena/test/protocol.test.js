import assert from "node:assert/strict";
import { test } from "node:test";
import { parseClientMessage, MAX_MESSAGE_CHARS, ERROR_CODES } from "../src/protocol.js";

test("hello accepts optional bounded names and strips control characters", () => {
  assert.deepEqual(parseClientMessage('{"type":"hello"}'), { ok: true, message: { type: "hello", name: "" } });
  assert.deepEqual(
    parseClientMessage('{"type":"hello","name":"しおん"}'),
    { ok: true, message: { type: "hello", name: "しおん" } },
  );
  const stripped = parseClientMessage('{"type":"hello","name":"a\\u0001b\\u200bc"}');
  assert.deepEqual(stripped, { ok: true, message: { type: "hello", name: "abc" } });
  const twenty = "あ".repeat(20);
  assert.equal(parseClientMessage(JSON.stringify({ type: "hello", name: twenty })).ok, true);
  const twentyOne = "あ".repeat(21);
  assert.deepEqual(parseClientMessage(JSON.stringify({ type: "hello", name: twentyOne })), {
    ok: false,
    code: ERROR_CODES.INVALID_MESSAGE,
  });
  for (const bad of ['{"type":"hello","name":42}', '{"type":"hello","name":["x"]}']) {
    assert.equal(parseClientMessage(bad).ok, false, bad);
  }
});

test("move requires a strict a1..h8 coordinate", () => {
  assert.deepEqual(parseClientMessage('{"type":"move","coord":"d3"}'), {
    ok: true,
    message: { type: "move", coord: "d3" },
  });
  for (const bad of [
    '{"type":"move"}',
    '{"type":"move","coord":"i1"}',
    '{"type":"move","coord":"a0"}',
    '{"type":"move","coord":"a9"}',
    '{"type":"move","coord":"A1"}',
    '{"type":"move","coord":"d33"}',
    '{"type":"move","coord":3}',
    '{"type":"move","coord":"dd"}',
  ]) {
    assert.equal(parseClientMessage(bad).ok, false, bad);
  }
});

test("resign, rematch and ping pass through", () => {
  for (const type of ["resign", "rematch", "ping"]) {
    assert.deepEqual(parseClientMessage(JSON.stringify({ type })), { ok: true, message: { type } });
  }
});

test("garbage is rejected with fixed codes", () => {
  assert.equal(parseClientMessage("{not json").code, ERROR_CODES.INVALID_JSON);
  assert.equal(parseClientMessage("[1,2,3]").code, ERROR_CODES.INVALID_MESSAGE);
  assert.equal(parseClientMessage("42").code, ERROR_CODES.INVALID_MESSAGE);
  assert.equal(parseClientMessage("null").code, ERROR_CODES.INVALID_MESSAGE);
  assert.equal(parseClientMessage('{"type":"nope"}').code, ERROR_CODES.UNKNOWN_TYPE);
  assert.equal(parseClientMessage('{"kind":"move"}').code, ERROR_CODES.INVALID_MESSAGE);
  assert.equal(parseClientMessage('{"type":123}').code, ERROR_CODES.INVALID_MESSAGE);
});

test("oversized frames are rejected before parsing", () => {
  const big = `{"type":"hello","name":"${"あ".repeat(1100)}"}`;
  assert.ok(big.length > MAX_MESSAGE_CHARS);
  assert.equal(parseClientMessage(big).code, ERROR_CODES.TOO_LARGE);
  assert.equal(parseClientMessage("x".repeat(MAX_MESSAGE_CHARS + 1)).code, ERROR_CODES.TOO_LARGE);
  const buffer = new TextEncoder().encode("x".repeat(MAX_MESSAGE_CHARS + 1)).buffer;
  assert.equal(parseClientMessage(buffer).code, ERROR_CODES.TOO_LARGE);
});

test("binary frames decode as UTF-8 text", () => {
  const encoded = new TextEncoder().encode('{"type":"ping"}').buffer;
  assert.deepEqual(parseClientMessage(encoded), { ok: true, message: { type: "ping" } });
  assert.equal(parseClientMessage(123).code, ERROR_CODES.INVALID_MESSAGE);
});
