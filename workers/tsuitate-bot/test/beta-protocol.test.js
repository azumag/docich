import test from "node:test";
import assert from "node:assert/strict";
import { MoveGate, ProtocolError, parsePlayerView, toBrainObservation } from "../src/adapters/beta.js";

function view(changes = {}) {
  return {
    gameId: "game-1", yourColor: "sente",
    yourPieces: [{ square: "5i", role: "king" }, { square: "7g", role: "pawn" }],
    yourHand: { pawn: 1 }, turn: "sente", moveNumber: 1,
    clocks: { senteMs: 300000, goteMs: 300000, running: "sente", serverTime: 1000 },
    fouls: { you: 0, opponent: 0 }, youInCheck: false, opponentInCheck: false,
    status: "playing", ...changes,
  };
}

function ready(raw = view()) {
  const gate = new MoveGate();
  const generation = gate.newConnection();
  assert.equal(gate.acceptView(raw, generation, { synchronized: true }), true);
  return { gate, generation };
}

function advanced(number = 2, turn = "gote", changes = {}) {
  const raw = view({ moveNumber: number, turn, ...changes });
  raw.clocks.running = raw.status === "ended" ? null : turn;
  raw.yourPieces[1].square = "7f";
  return raw;
}

function ended(changes = {}) {
  return advanced(2, "gote", { status: "ended", ...changes });
}

function errorCode(code) {
  return (error) => error instanceof ProtocolError && error.code === code && error.message === code;
}

test("PlayerView projects only allowed fields and stays detached from the input", () => {
  const raw = view();
  raw.token = "tsb_do_not_publish";
  raw.opponent = { username: "hidden" };
  raw.opponentPieces = [{ square: "5a", role: "king" }];
  raw.yourPieces[0].secret = "hidden";
  raw.clocks.secret = "hidden";
  raw.fouls.secret = "hidden";
  const parsed = parsePlayerView(raw);
  raw.yourPieces[0].square = "1a";
  raw.yourHand.pawn = 20;
  assert.deepEqual(parsed.yourHand, { pawn: 1 });
  assert.deepEqual(parsed.yourPieces, [{ square: "5i", role: "king" }, { square: "7g", role: "pawn" }]);
  assert.doesNotMatch(JSON.stringify(parsed), /hidden|tsb_|opponentPieces|username|secret/);
  assert.throws(() => { parsed.yourPieces[0].square = "1a"; }, TypeError);
  assert.throws(() => { parsed.yourHand.pawn = 30; }, TypeError);
});

test("beta conversion maps promoted roles and both colors without hidden-board leakage", () => {
  const roles = ["pawn", "lance", "knight", "silver", "gold", "bishop", "rook", "king",
    "tokin", "promotedlance", "promotedknight", "promotedsilver", "horse", "dragon"];
  const usi = ["P", "L", "N", "S", "G", "B", "R", "K", "+P", "+L", "+N", "+S", "+B", "+R"];
  for (const side of ["sente", "gote"]) {
    const raw = view({ yourColor: side, yourPieces: roles.map((role, index) => ({
      square: `${(index % 9) + 1}${index < 9 ? "a" : "b"}`, role,
    })), youInCheck: true });
    raw.opponentPieces = [{ square: "9i", role: "king" }];
    const observation = toBrainObservation(raw);
    assert.equal(observation.ruleset, "tsuitate-9x9");
    assert.equal(observation.color, side === "sente" ? "b" : "w");
    assert.equal(observation.turn, "b");
    assert.equal(observation.inCheck, true);
    assert.equal(observation.opponentInCheck, false);
    assert.equal(observation.hand.P, 1);
    assert.equal(observation.pieces.length, roles.length);
    roles.forEach((role, index) => {
      const square = `${(index % 9) + 1}${index < 9 ? "a" : "b"}`;
      assert.equal(observation.pieces.find((piece) => piece.square === square).role, usi[index]);
    });
    assert.doesNotMatch(JSON.stringify(observation), /gameId|clock|foul|opponentPieces|yourColor/);
  }
  assert.equal(toBrainObservation(view({ yourColor: "unknown" })), null);
});

test("malformed known fields cannot become playable observations", () => {
  const cases = [
    ["gameId", ""], ["gameId", "bad\nvalue"], ["gameId", " leading"], ["yourColor", "other"],
    ["turn", true], ["moveNumber", true], ["moveNumber", "1"], ["moveNumber", 0],
    ["moveNumber", Number.MAX_SAFE_INTEGER + 1], ["youInCheck", 1], ["opponentInCheck", null],
    ["status", "unknown"], ["yourHand", { king: 1 }], ["yourHand", { pawn: true }],
    ["yourHand", { pawn: 40 }], ["yourPieces", [{ square: "0a", role: "pawn" }]],
    ["yourPieces", [{ square: "5i", role: "secret" }]],
    ["yourPieces", [{ square: "5i", role: "king" }, { square: "5i", role: "pawn" }]],
    ["yourPieces", [{ square: "5i", role: "king", color: "gote" }]],
    ["yourPieces", [{ square: "5i", role: "king", owner: "gote" }]],
    ["yourPieces", [{ square: "5i\n", role: "king" }]], ["fouls", { you: 11, opponent: 0 }],
  ];
  for (const [field, value] of cases) {
    assert.throws(() => parsePlayerView(view({ [field]: value })), (error) => {
      assert.ok(error instanceof ProtocolError);
      assert.match(error.message, /^invalid_[a-z_]+$/);
      return true;
    }, field);
  }
});

test("input accessors and prototypes cannot supply trusted observation fields", () => {
  const raw = view();
  Object.defineProperty(raw, "gameId", { get() { throw new Error("secret"); } });
  assert.throws(() => parsePlayerView(raw), errorCode("invalid_game_id"));
  assert.throws(() => parsePlayerView(Object.create(view())), errorCode("invalid_view"));
});

test("clock values are finite, nonnegative numbers; running must match game status", () => {
  for (const value of [true, null, "3", -1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    const raw = view();
    raw.clocks.senteMs = value;
    assert.throws(() => parsePlayerView(raw), errorCode("invalid_clocks"));
  }
  const raw = view();
  raw.clocks.senteMs = 0;
  raw.clocks.goteMs = 1000.5;
  assert.equal(parsePlayerView(raw).clocks.goteMs, 1000.5);
  raw.clocks.running = null;
  assert.throws(() => parsePlayerView(raw), errorCode("invalid_running_clock"));
  const end = ended();
  end.clocks.running = "sente";
  assert.throws(() => parsePlayerView(end), errorCode("invalid_running_clock"));
});

test("initial synchronization and own turn are required; duplicate state never allows two intents", () => {
  const gate = new MoveGate();
  assert.equal(gate.canMove, false);
  assert.throws(() => gate.prepareMove("7g7f"), errorCode("move_not_ready"));
  const generation = gate.newConnection();
  assert.equal(gate.acceptView(view(), generation), false);
  assert.equal(gate.acceptView(view(), generation, { synchronized: true }), true);
  assert.equal(gate.canMove, true);
  const intent = gate.prepareMove("7g7f");
  assert.deepEqual(intent.payload, { gameId: "game-1", usi: "7g7f" });
  assert.equal(intent.gameId, "game-1");
  assert.equal(intent.moveNumber, 1);
  assert.equal(gate.acceptView(view(), generation), true);
  assert.throws(() => gate.prepareMove("P*5e"), errorCode("move_not_ready"));
  assert.equal(gate.acknowledge(intent, { ok: true }), true);
  assert.equal(gate.acknowledge(intent, { ok: true }), false);
  assert.equal(gate.view.moveNumber, 1);
  assert.equal(gate.pending, intent);
  assert.equal(gate.needsSync, true);
  assert.equal(gate.acceptView(advanced(), generation), true);
  assert.equal(gate.pending, null);
  assert.equal(gate.canMove, false);
});

test("advanced view before ACK ignores the late success callback", () => {
  const { gate, generation } = ready();
  const intent = gate.prepareMove("7g7f");
  assert.equal(gate.acceptView(advanced(), generation), true);
  assert.equal(gate.acknowledge(intent, { ok: true }), false);
  assert.equal(gate.view.moveNumber, 2);
  assert.equal(gate.needsSync, false);
});

test("foul ACK requires its confirmed count before allowing a different move", () => {
  const { gate, generation } = ready();
  const intent = gate.prepareMove("7g7f");
  assert.equal(gate.acknowledge(intent, { ok: false, reason: "foul", foulCount: 1 }), true);
  assert.equal(gate.acceptView(view(), generation, { synchronized: true }), false);
  assert.equal(gate.canMove, false);
  assert.equal(gate.acceptView(view({ fouls: { you: 1, opponent: 0 } }), generation, { synchronized: true }), true);
  assert.throws(() => gate.prepareMove("7g7f"), errorCode("already_attempted"));
  assert.equal(gate.prepareMove("P*5e").usi, "P*5e");
});

test("foul view before ACK cannot let that ACK clear a newer pending move", () => {
  const { gate, generation } = ready();
  const first = gate.prepareMove("7g7f");
  assert.equal(gate.acceptView(view({ fouls: { you: 1, opponent: 0 } }), generation), true);
  const second = gate.prepareMove("P*5e");
  assert.equal(gate.acknowledge(first, { ok: false, reason: "foul", foulCount: 1 }), false);
  assert.equal(gate.pending, second);
});

test("foul exclusions reset for an advanced position, never for duplicate or opponent-foul views", () => {
  const { gate, generation } = ready();
  gate.prepareMove("7g7f");
  gate.acceptView(view({ fouls: { you: 1, opponent: 0 } }), generation);
  const attempts = gate.attemptedMoves;
  attempts.clear();
  gate.acceptView(view({ fouls: { you: 1, opponent: 1 } }), generation);
  assert.throws(() => gate.prepareMove("7g7f"), errorCode("already_attempted"));
  gate.acceptView(advanced(3, "sente", { fouls: { you: 1, opponent: 1 } }), generation);
  assert.deepEqual([...gate.attemptedMoves], []);
  assert.equal(gate.prepareMove("7g7f").usi, "7g7f");
});

test("timeout and reconnect never resend a move from an unchanged position", () => {
  const { gate, generation } = ready();
  const intent = gate.prepareMove("7g7f");
  gate.timeout(intent);
  assert.equal(gate.acceptView(view(), generation, { synchronized: true }), true);
  assert.equal(gate.pending, intent);
  gate.disconnect(generation);
  const nextGeneration = gate.newConnection();
  assert.equal(gate.acceptView(advanced(), generation, { synchronized: true }), false);
  assert.equal(gate.acknowledge(intent, { ok: true }), false);
  assert.equal(gate.acceptView(view(), nextGeneration, { synchronized: true }), true);
  assert.throws(() => gate.prepareMove("P*5e"), errorCode("move_not_ready"));
  assert.equal(gate.acceptView(advanced(), nextGeneration, { synchronized: true }), true);
  assert.equal(gate.pending, null);
});

test("old connection callbacks cannot poison the current playable board", () => {
  const { gate, generation } = ready();
  assert.equal(gate.acceptView({ token: "secret" }, generation - 1), false);
  gate.disconnect(generation - 1);
  assert.equal(gate.connected, true);
  assert.equal(gate.needsSync, false);
  assert.equal(gate.canMove, true);
});

test("stale ply and decreasing foul counts are ignored; conflicting current boards require sync", () => {
  const { gate, generation } = ready();
  gate.acceptView(advanced(3, "sente", { fouls: { you: 1, opponent: 1 } }), generation);
  const before = gate.view;
  assert.equal(gate.acceptView(view(), generation, { synchronized: true }), false);
  assert.equal(gate.acceptView(advanced(4, "gote", { fouls: { you: 0, opponent: 1 } }), generation), false);
  assert.equal(gate.acceptView(advanced(4, "gote", { fouls: { you: 1, opponent: 0 } }), generation), false);
  const bad = advanced(3, "sente", { fouls: { you: 1, opponent: 1 } });
  bad.yourPieces[1].square = "7e";
  assert.throws(() => gate.acceptView(bad, generation), errorCode("conflicting_position"));
  assert.equal(gate.view, before);
  assert.equal(gate.needsSync, true);
  assert.throws(() => gate.acceptView(view({ gameId: "another" }), generation), errorCode("unexpected_game"));
  assert.equal(gate.view, before);
});

test("unordered pieces and explicit zero hand counts are the same position", () => {
  const { gate, generation } = ready();
  const raw = view();
  raw.yourPieces.reverse();
  raw.yourHand.rook = 0;
  assert.equal(gate.acceptView(raw, generation), true);
  assert.equal(gate.canMove, true);
});

test("invalid current views retain evidence and prevent using the preceding good board", () => {
  for (const raw of [view({ moveNumber: true }), view({ gameId: "other" }), null]) {
    const { gate, generation } = ready();
    const before = gate.view;
    if (raw === null) assert.equal(gate.acceptView(raw, generation, { synchronized: true }), false);
    else assert.throws(() => gate.acceptView(raw, generation), ProtocolError);
    assert.equal(gate.view, before);
    assert.equal(gate.needsSync, true);
    assert.equal(gate.canMove, false);
    assert.equal(gate.acceptView(view(), generation), false);
    assert.equal(gate.acceptView(view(), generation, { synchronized: true }), true);
    assert.equal(gate.canMove, true);
  }
});

test("invalid ACK preserves the ambiguous intent and never includes server content in its error", () => {
  for (const raw of [{ ok: 1 }, { ok: false, reason: "secret" },
    { ok: false, reason: "foul", foulCount: true }, { ok: false, reason: "foul", foulCount: 0 },
    { ok: false, reason: "error" }]) {
    const { gate } = ready();
    const intent = gate.prepareMove("7g7f");
    assert.throws(() => gate.acknowledge(intent, raw), errorCode("invalid_ack"));
    assert.equal(gate.pending, intent);
    assert.equal(gate.canMove, false);
    assert.equal(gate.needsSync, true);
  }
});

test("a valid error ACK still requires synchronization and retains the attempted move exclusion", () => {
  const { gate, generation } = ready();
  const intent = gate.prepareMove("7g7f");
  assert.equal(gate.acknowledge(intent, { ok: false, reason: "error", error: "untrusted server message" }), true);
  assert.equal(gate.pending, null);
  assert.equal(gate.canMove, false);
  assert.doesNotMatch(JSON.stringify(gate.checkpoint()), /untrusted/);
  gate.acceptView(view(), generation, { synchronized: true });
  assert.throws(() => gate.prepareMove("7g7f"), errorCode("already_attempted"));
  assert.equal(gate.prepareMove("P*5e").usi, "P*5e");
});

test("quiesce blocks matchmaking while allowing the current game to finish", () => {
  const { gate, generation } = ready();
  gate.requestQuiesce();
  assert.equal(gate.canJoinQueue, false);
  assert.equal(gate.canMove, true);
  const intent = gate.prepareMove("7g7f");
  assert.equal(gate.acceptView(ended(), generation), true);
  assert.equal(gate.pending, null);
  assert.equal(gate.canJoinQueue, false);
  assert.equal(gate.acknowledge(intent, { ok: true }), false);
  assert.equal(gate.acceptView(advanced(3, "sente"), generation, { synchronized: true }), false);
  assert.equal(gate.canMove, false);
});

test("only an ended view enables a new game; missing active state is not end evidence", () => {
  const gate = new MoveGate();
  const generation = gate.newConnection();
  assert.equal(gate.canJoinQueue, false);
  assert.equal(gate.acceptView(null, generation, { synchronized: true }), true);
  assert.equal(gate.canJoinQueue, true);
  gate.acceptView(view(), generation, { synchronized: true });
  assert.equal(gate.canJoinQueue, false);
  assert.equal(gate.acceptView(null, generation, { synchronized: true }), false);
  assert.equal(gate.canJoinQueue, false);
  gate.acceptView(ended(), generation, { synchronized: true });
  assert.equal(gate.canJoinQueue, true);
  gate.acceptView(view({ gameId: "game-2" }), generation, { synchronized: true });
  assert.equal(gate.canMove, true);
  assert.equal(gate.view.gameId, "game-2");
  assert.throws(() => gate.acceptView(ended(), generation), errorCode("unexpected_game"));
  assert.equal(gate.view.gameId, "game-2");
});

test("the tenth own foul blocks further input", () => {
  const { gate } = ready(view({ fouls: { you: 10, opponent: 0 } }));
  assert.equal(gate.canMove, false);
  assert.throws(() => gate.prepareMove("7g7f"), errorCode("move_not_ready"));
});

test("USI accepts strict shogi move syntax only", () => {
  for (const usi of ["", "7g7g", "0a1b", "P*0a", "7g7f++", "7g7f\n", "K*5e", null]) {
    const { gate } = ready();
    assert.throws(() => gate.prepareMove(usi), errorCode("invalid_usi"));
    assert.equal(gate.pending, null);
  }
  for (const usi of ["7g7f", "8h2b+", "P*5e"]) {
    const { gate } = ready();
    assert.equal(gate.prepareMove(usi).usi, usi);
  }
});

test("durable checkpoint preserves unresolved sends across a process restart", () => {
  const { gate } = ready();
  const intent = gate.prepareMove("7g7f");
  const snapshot = JSON.parse(JSON.stringify(gate.checkpoint()));
  const recovered = new MoveGate();
  assert.equal(recovered.restore(snapshot), true);
  assert.equal(recovered.connected, false);
  assert.equal(recovered.needsSync, true);
  assert.ok(recovered.generation > intent.generation);
  const generation = recovered.newConnection();
  assert.equal(recovered.acceptView(view(), generation, { synchronized: true }), true);
  assert.equal(recovered.canMove, false);
  assert.equal(recovered.pending.usi, "7g7f");
  assert.equal(recovered.acknowledge(recovered.pending, { ok: true }), false);
  assert.deepEqual([...recovered.attemptedMoves], ["7g7f"]);
  recovered.acceptView(advanced(3, "sente"), generation, { synchronized: true });
  assert.equal(recovered.pending, null);
  assert.equal(recovered.canMove, true);
});

test("a persisted success ACK remains unresolved until position evidence arrives", () => {
  const { gate } = ready();
  const intent = gate.prepareMove("7g7f");
  gate.acknowledge(intent, { ok: true });
  const recovered = new MoveGate();
  recovered.restore(JSON.parse(JSON.stringify(gate.checkpoint())));
  const generation = recovered.newConnection();
  recovered.acceptView(view(), generation, { synchronized: true });
  assert.equal(recovered.canMove, false);
  recovered.acceptView(ended(), generation, { synchronized: true });
  assert.equal(recovered.pending, null);
  assert.equal(recovered.canJoinQueue, true);
});

test("restored foul ACK rejects stale sync and prevents repetition of the same foul", () => {
  const { gate } = ready();
  const intent = gate.prepareMove("7g7f");
  gate.acknowledge(intent, { ok: false, reason: "foul", foulCount: 1 });
  const recovered = new MoveGate();
  recovered.restore(JSON.parse(JSON.stringify(gate.checkpoint())));
  const generation = recovered.newConnection();
  assert.equal(recovered.acceptView(view(), generation, { synchronized: true }), false);
  recovered.acceptView(view({ fouls: { you: 1, opponent: 0 } }), generation, { synchronized: true });
  assert.throws(() => recovered.prepareMove("7g7f"), errorCode("already_attempted"));
  assert.equal(recovered.prepareMove("P*5e").usi, "P*5e");
});

test("a restored ambiguous send is resolved by a higher foul count without losing its exclusion", () => {
  const { gate } = ready();
  gate.prepareMove("7g7f");
  const recovered = new MoveGate();
  recovered.restore(JSON.parse(JSON.stringify(gate.checkpoint())));
  const generation = recovered.newConnection();
  assert.equal(recovered.acceptView(view({ fouls: { you: 1, opponent: 0 } }), generation, { synchronized: true }), true);
  assert.equal(recovered.canMove, true);
  assert.throws(() => recovered.prepareMove("7g7f"), errorCode("already_attempted"));
});

test("checkpoint projection cannot retain unknown fields, ACK content, or transport secrets", () => {
  const { gate } = ready(view({ token: "tsb_secret", opponentPieces: [{ hidden: true }] }));
  gate.prepareMove("7g7f");
  gate.requestQuiesce();
  const snapshot = JSON.parse(JSON.stringify(gate.checkpoint()));
  snapshot.token = "tsb_secret";
  snapshot.pending.payload.token = "tsb_secret";
  snapshot.view.opponent = { username: "hidden" };
  const recovered = new MoveGate();
  recovered.restore(snapshot);
  const safe = JSON.stringify(recovered.checkpoint());
  assert.doesNotMatch(safe, /tsb_|token|hidden|username|opponentPieces/);
  assert.equal(recovered.quiescing, true);
});

test("invalid checkpoints are rejected atomically without changing the live gate", () => {
  const { gate } = ready();
  gate.prepareMove("7g7f");
  const base = JSON.parse(JSON.stringify(gate.checkpoint()));
  const corruptions = [
    (s) => { s.version = 2; }, (s) => { s.generation = -1; },
    (s) => { s.generation = Number.MAX_SAFE_INTEGER; }, (s) => { s.sequence = false; },
    (s) => { s.ackedSequence = 2; }, (s) => { s.foulFloor = 11; },
    (s) => { s.quiescing = "yes"; }, (s) => { s.attemptedMoves = ["secret"]; },
    (s) => { s.attemptedMoves = ["7g7f", "7g7f"]; }, (s) => { s.attemptedMoves = []; },
    (s) => { s.pending.gameId = "other"; }, (s) => { s.pending.moveNumber = 2; },
    (s) => { s.pending.generation = 2; }, (s) => { s.pending.payload.usi = "P*5e"; },
    (s) => { s.pending.sequence = 0; }, (s) => { s.view = null; },
    (s) => { s.view.moveNumber = true; }, (s) => { delete s.pending; },
  ];
  for (const corrupt of corruptions) {
    const snapshot = structuredClone(base);
    corrupt(snapshot);
    const before = gate.checkpoint();
    assert.throws(() => gate.restore(snapshot), errorCode("invalid_checkpoint"));
    assert.deepEqual(gate.checkpoint(), before);
    assert.equal(gate.connected, true);
  }
});
