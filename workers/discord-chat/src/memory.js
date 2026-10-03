export const RECENT_LIMIT = 6;
export const RECALL_LIMIT = 4;

export function searchTerms(text, limit = 256) {
  const normalized = String(text ?? "").normalize("NFKC").toLowerCase();
  const terms = new Set();
  const words = normalized.match(/[a-z0-9_]{2,}|[\u3040-\u30ff\u3400-\u9fff]+/g) ?? [];
  for (const word of words) {
    if (/^[a-z0-9_]+$/.test(word)) {
      terms.add(word);
    } else {
      for (let i = 0; i + 1 < word.length; i += 1) terms.add(word.slice(i, i + 2));
    }
  }
  return [...terms].sort().slice(0, limit);
}

export function initializeMemory(sql) {
  sql.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      message_id TEXT NOT NULL UNIQUE,
      guild_id TEXT NOT NULL,
      channel_id TEXT NOT NULL,
      author_id TEXT NOT NULL,
      author_name TEXT NOT NULL,
      content TEXT NOT NULL,
      reference_id TEXT,
      created_at REAL NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','failed','deleted')),
      reply_id TEXT UNIQUE,
      reply TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS scope_recent ON conversations
      (guild_id, channel_id, author_id, state, seq DESC);
    CREATE TABLE IF NOT EXISTS terms (
      term TEXT NOT NULL,
      seq INTEGER NOT NULL REFERENCES conversations(seq),
      PRIMARY KEY(term, seq)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS terms_source ON terms(seq);
  `);
  sql.exec(`
    UPDATE conversations
       SET state='failed', content='', author_name='', reply=''
     WHERE state IN ('pending','sending')
  `);
}

export function beginConversation(sql, event) {
  const existing = sql.exec(
    "SELECT seq FROM conversations WHERE message_id=?",
    String(event.id),
  ).toArray();
  if (existing.length) return null;
  return sql.exec(`
    INSERT INTO conversations
      (message_id,guild_id,channel_id,author_id,author_name,content,reference_id,created_at,state)
    VALUES (?,?,?,?,?,?,?,?,'pending')
    RETURNING seq
  `,
  String(event.id),
  String(event.guildId),
  String(event.channelId),
  String(event.authorId),
  String(event.authorName ?? "").slice(0, 80),
  String(event.content ?? "").slice(0, 2000),
  event.referenceId ? String(event.referenceId) : null,
  Number(event.createdAt),
  ).one().seq;
}

export function isActive(sql, seq) {
  const rows = sql.exec("SELECT state FROM conversations WHERE seq=?", seq).toArray();
  return rows.length === 1 && rows[0].state === "pending";
}

export function markSending(sql, seq) {
  if (!isActive(sql, seq)) return false;
  sql.exec("UPDATE conversations SET state='sending' WHERE seq=?", seq);
  return true;
}

export function failConversation(sql, seq) {
  sql.exec(`
    UPDATE conversations
       SET state='failed', content='', author_name='', reply=''
     WHERE seq=? AND state IN ('pending','sending')
  `, seq);
}

export function finishConversation(sql, seq, replyId, reply) {
  const rows = sql.exec(
    "SELECT content,state FROM conversations WHERE seq=?",
    seq,
  ).toArray();
  if (rows.length !== 1 || rows[0].state !== "sending") return false;
  sql.exec(
    "UPDATE conversations SET state='sent',reply_id=?,reply=? WHERE seq=?",
    String(replyId), String(reply), seq,
  );
  for (const term of searchTerms(rows[0].content)) {
    sql.exec("INSERT OR IGNORE INTO terms(term,seq) VALUES (?,?)", term, seq);
  }
  return true;
}

export function memoryContext(sql, event, beforeSeq) {
  const scope = [String(event.guildId), String(event.channelId), String(event.authorId)];
  const recent = sql.exec(`
    SELECT * FROM conversations
     WHERE guild_id=? AND channel_id=? AND author_id=?
       AND seq<? AND state='sent'
     ORDER BY seq DESC LIMIT ?
  `, ...scope, beforeSeq, RECENT_LIMIT).toArray();
  const cutoff = recent.length ? Math.min(...recent.map((row) => row.seq)) : beforeSeq;
  const tokens = searchTerms(event.content, 48);
  let recalled = [];
  if (tokens.length) {
    const placeholders = tokens.map(() => "?").join(",");
    recalled = sql.exec(`
      SELECT c.*, COUNT(*) AS matches
        FROM conversations c JOIN terms t ON t.seq=c.seq
       WHERE c.guild_id=? AND c.channel_id=? AND c.author_id=?
         AND c.seq<? AND c.state='sent'
         AND t.term IN (${placeholders})
       GROUP BY c.seq
       ORDER BY matches DESC,c.seq DESC LIMIT ?
    `, ...scope, cutoff, ...tokens, RECALL_LIMIT).toArray();
  }
  const bySeq = new Map();
  for (const row of [...recent, ...recalled]) bySeq.set(row.seq, row);
  const rows = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  const messages = [];
  for (const row of rows) {
    messages.push({
      role: "user",
      content: JSON.stringify({
        source: "stored_conversation",
        message_id: row.message_id,
        author_id: row.author_id,
        name: row.author_name,
        created_at: row.created_at,
        text: row.content,
      }),
    });
    messages.push({ role: "assistant", content: row.reply });
  }
  return { messages, sources: rows.map((row) => row.seq) };
}

export function validContext(sql, currentSeq, context) {
  if (!isActive(sql, currentSeq)) return false;
  if (!context.sources.length) return true;
  const placeholders = context.sources.map(() => "?").join(",");
  const row = sql.exec(
    `SELECT COUNT(*) AS count FROM conversations WHERE state='sent' AND seq IN (${placeholders})`,
    ...context.sources,
  ).one();
  return Number(row.count) === context.sources.length;
}

export function forgetScope(sql, guildId, channelId, { messageIds = [], authorId = null } = {}) {
  if (authorId === null && messageIds.length === 0) return;
  let where = "guild_id=? AND channel_id=?";
  const params = [String(guildId), String(channelId)];
  if (authorId !== null) {
    where += " AND author_id=?";
    params.push(String(authorId));
  } else {
    const ids = messageIds.map(String);
    const placeholders = ids.map(() => "?").join(",");
    where += ` AND (message_id IN (${placeholders}) OR reply_id IN (${placeholders}))`;
    params.push(...ids, ...ids);
  }
  sql.exec(`DELETE FROM terms WHERE seq IN (SELECT seq FROM conversations WHERE ${where})`, ...params);
  sql.exec(`
    UPDATE conversations
       SET content='',author_name='',reply='',reference_id=NULL,state='deleted'
     WHERE ${where}
  `, ...params);
}
