import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { DISCORD_CONTEXT, generateReply } from "../src/llm.js";

const persona = readFileSync(new URL("../../../src/docich/comment/prompts/comment_persona_main.md", import.meta.url), "utf8").trim();
async function capture(event, history = []) {
  let input;
  await generateReply({DOCICH_PERSONA: persona, AI: {async run(_model, value) {
    input = value; return {response: "返答です。"};
  }}}, history, {id: "fixture", authorId: "7", content: "同志、こんにちは。", ...event});
  return input.messages;
}

test("voice call is the only latest reply target; ambient questions remain a separate reference", async () => {
  const context = [{userId: "8", text: "晩ご飯は何にする？"}, {userId: "9", text: "明日の天気は？"}];
  const history = [{role: "user", content: "以前の質問"}, {role: "assistant", content: "以前の返答"}];
  const messages = await capture({voice: true, voiceContext: context}, history);
  const target = JSON.parse(messages.at(-1).content);
  const reference = JSON.parse(messages.at(-2).content);
  assert.equal(target.source, "current_voice_call");
  assert.equal(target.text, "同志、こんにちは。");
  assert.equal(target.recent_voice_context, undefined);
  assert.equal(reference.source, "voice_background_context");
  assert.equal(reference.purpose, "reference_only");
  assert.deepEqual(reference.recent_voice_context, context);
  assert.deepEqual(messages.slice(1, 3), history);
  assert.match(messages[0].content, /返答の対象は最後のcurrent_voice_callのtextだけ/);
  assert.match(messages[0].content, /過去の質問をまとめて回答/);
  assert.match(messages[0].content, /挨拶には挨拶/);
});

test("voice situation overrides the old game setting even without ambient context", async () => {
  for (const event of [{voice: true}, {voiceContext: []}]) {
    const messages = await capture(event);
    assert.equal(messages.length, 2);
    assert.match(messages[0].content, /Fly Me to the Home/);
    assert.match(messages[0].content, /通称「ツ」/);
    assert.match(messages[0].content, /並走会/);
    assert.match(messages[0].content, /以前のゲーム・配信設定より優先/);
    assert.match(messages[0].content, /自分がゲームを操作しているとは言いません/);
    assert.equal(JSON.parse(messages.at(-1).content).source, "current_voice_call");
  }
});

test("text mentions retain the canonical persona and original message shape", async () => {
  const messages = await capture({content: "元気ですか？"});
  assert.equal(messages[0].content, persona + "\n\n" + DISCORD_CONTEXT);
  assert.equal(messages.length, 2);
  assert.equal(JSON.parse(messages[1].content).source, undefined);
  assert.equal(JSON.parse(messages[1].content).text, "元気ですか？");
});


test("voice preserves canonical character traits without conflicting Twitch/game role", async () => {
  const messages = await capture({voice: true});
  const system = messages[0].content;
  assert.doesNotMatch(system, /ソ連ゲーム|自分自身が|プレイヤー当事者/);
  for (const line of persona.split(/\r?\n/u).filter(line =>
    !line.startsWith("あなたはTwitch配信「ソ連ゲーム」") &&
    !line.startsWith("ゲームの話をするときはプレイヤー当事者として語ること。")
  )) assert.ok(system.includes(line));
  assert.match(system, /ウィットや比喩も今回の呼びかけに沿う/);
  assert.match(system, /背景会話から別の話題を持ち出しません/);
});
