import { memoryContext } from "./memory.js";
import { generateReply } from "./llm.js";

/** Internal generation only: caller owns admission, dedup, deletion recheck,
 * delivery and memory completion. No transport, new prompt or voice policy.
 */
export async function generateConversationReply(env, sql, event, seq, setStage = () => {}) {
  setStage("memory_context");
  const context = memoryContext(sql, event, seq);
  setStage("workers_ai");
  const reply = await generateReply(env, context.messages, event);
  return { reply, context };
}
