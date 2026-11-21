import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { Journal } from "../src/journal.js";

test("privacy erases terminal copies but preserves unresolved physical intents", async () => {
  const path = await mkdtemp(join(tmpdir(), "horizon-privacy-"));
  try {
    const journal = new Journal(path);
    await journal.init();
    const user = randomUUID(),
      terminal = randomUUID(),
      pending = randomUUID();
    await journal.write(terminal, {
      status: "COMPLETED",
      user_id: user,
      nickname: "Player",
    });
    await journal.write(pending, { status: "PROCESSING", user_id: user });
    assert.deepEqual(await journal.minimize({ user_id: user }), {
      removed: 1,
      blocked: true,
    });
    await assert.rejects(readFile(join(path, terminal + ".json")));
    assert.equal((await journal.pending())[0].id, pending);
    assert.deepEqual(
      await journal.minimize({ now: Date.now() + 500 * 86400000 }),
      { removed: 0, blocked: false },
    );
  } finally {
    await rm(path, { recursive: true, force: true });
  }
});
