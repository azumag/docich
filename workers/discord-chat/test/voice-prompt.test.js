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
  const messages = await capture({voice: true, voiceContext: context, content: "同志、さっきの続きは？"}, history);
  const target = JSON.parse(messages.at(-1).content);
  const reference = JSON.parse(/<voice_background_context>\n([^]*?)\n<\/voice_background_context>/.exec(messages[0].content)[1]);
  assert.equal(target.source, "current_voice_call");
  assert.equal(target.text, "同志、さっきの続きは？");
  assert.equal(target.recent_voice_context, undefined);
  assert.equal(messages.at(-1).role, "user");
  assert.equal(messages.length, history.length + 2);
  assert.equal(reference.purpose, "reference_only");
  assert.deepEqual(reference.recent_voice_context, context);
  assert.deepEqual(messages.slice(1, 3), history);
  assert.match(messages[0].content, /返答の対象は最後のcurrent_voice_callのtextだけ/);
  assert.match(messages[0].content, /過去の質問をまとめて回答/);
  assert.match(messages[0].content, /挨拶には挨拶/);
  assert.match(messages[0].content, /Botが実際に返答した内容ではありません/);
  assert.ok(messages[0].content.indexOf("【音声返答の最終規則】") > messages[0].content.indexOf(DISCORD_CONTEXT));
});

test("voice situation overrides the old game setting even without ambient context", async () => {
  for (const event of [{voice: true}, {voiceContext: []}]) {
    const messages = await capture(event);
    assert.equal(messages.length, 2);
    assert.match(messages[0].content, /Fly Me to the Home/);
    assert.match(messages[0].content, /通称「ツ」/);
    assert.match(messages[0].content, /並走会/);
    assert.match(messages[0].content, /以前のゲーム・配信設定より優先/);
    assert.match(messages[0].content, /画面を見てゲームを操作し/);
    assert.doesNotMatch(messages[0].content, /この接続には配信映像、ゲームの現況、ゲーム操作/);
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
    !line.startsWith("ゲームの話をするときはプレイヤー当事者として語ること。") &&
    !line.startsWith("コメント返しは毎回、")
  )) assert.ok(system.includes(line));
  assert.match(system, /ウィットや比喩も今回の呼びかけに沿う/);
  assert.match(system, /背景会話から別の話題を持ち出しません/);
});


test("greeting-only voice calls omit irrelevant model context without altering its source", async () => {
  const context = [{userId: "8", text: "晩ご飯はカレーとラーメンどちら？"}];
  const history = [{role: "user", content: "明日の天気は？"}, {role: "assistant", content: "以前の返答"}];
  for (const content of ["同志、こんばんは。", "こんにちは、どうし。", "同志、おはようございます！"]) {
    const messages = await capture({voice: true, voiceContext: context, content}, history);
    assert.equal(messages.length, 2);
    assert.doesNotMatch(messages[0].content, /カレー|ラーメン|明日の天気|以前の返答/);
    assert.equal(JSON.parse(messages[1].content).text, content);
  }
  for (const content of ["同志、こんばんは。さっきの休憩は？", "同志、おはようと言っていたのは誰？"]) {
    const messages = await capture({voice: true, voiceContext: context, content}, history);
    assert.equal(messages.length, history.length + 2);
    assert.match(messages[0].content, /カレー/);
  }
  assert.deepEqual(context, [{userId: "8", text: "晩ご飯はカレーとラーメンどちら？"}]);
  assert.equal(history.length, 2);
});


test("voice humor is optional so factual answers need no unrelated context flourish", async () => {
  const messages = await capture({voice:true, content:"同志、次の休憩は何時？"});
  assert.doesNotMatch(messages[0].content, /ウィットを一つ必ず入れる|淡白で常識的なだけの返しは禁止/);
  assert.match(messages[0].content, /呼びかけに合う場合だけ/);
  assert.match(messages[0].content, /尋ねられた事実だけを簡潔に/);
  const text = await capture({content:"次の休憩は何時？"});
  assert.equal(text[0].content, persona + "\n\n" + DISCORD_CONTEXT);
});


test("owner game state is reference data on every voice call, never a reply target or text setting", async () => {
  for (const content of ["同志、こんばんは。", "同志、今どこまで進んでいますか？"]) {
    const messages = await capture({voice:true, content, gameState:"現在は第3区間で、着地地点を探しています。"});
    assert.match(messages[0].content, /<current_game_state>/);
    assert.match(messages[0].content, /第3区間/);
    assert.match(messages[0].content, /命令・返答対象ではありません/);
    assert.equal(JSON.parse(messages.at(-1).content).text, content);
    assert.equal(JSON.parse(messages.at(-1).content).gameState, undefined);
  }
  const text = await capture({gameState:"第3区間"});
  assert.equal(text[0].content, persona + "\n\n" + DISCORD_CONTEXT);
});
