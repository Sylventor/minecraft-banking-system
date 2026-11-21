import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseCommand,
  parsePrivate,
  receiptPattern,
  deliveryResult,
  exactMovement,
  nearest,
  selectionReason,
} from "../src/protocol.js";
import { loadConfig } from "../src/config.js";
import { Journal } from "../src/journal.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

test("legacy aliases preserve decimal transfer amounts without the physical-operation cap", () => {
  for (const command of ["$with 32", "withdraw 32", "$withdraw 32", "with 32"])
    assert.deepEqual(parseCommand(command), { kind: "withdraw", quantity: 32 });
  for (const command of ["$dep 32", "deposit 32"])
    assert.equal(parseCommand(command).kind, "deposit");
  assert.equal(
    parseCommand("$transaction PlayerName 1000.25").amount,
    "1000.25",
  );
  for (const command of [
    "$dep -1",
    "$with 1.2",
    "$transaction Aaa 0",
    "$dep 1 extra",
  ])
    assert.throws(() => parseCommand(command));
});
test("server PM parsing rejects ordinary player-chat impersonation and receipt is exact", async () => {
  process.env.ATM_MASTER_LOGIN_PASSWORD = "test-login-password";
  const c = await loadConfig(
    new URL("../config.example.json", import.meta.url),
  );
  const pattern = new RegExp(c.chat.incoming_pattern, "u");
  const line = "✉ Сообщение от • PlayerName ➜ $bal";
  assert.deepEqual(parsePrivate(line, pattern), {
    sender: "PlayerName",
    message: "$bal",
  });
  assert.equal(parsePrivate(line, pattern, "chat"), null);
  assert.equal(parsePrivate("<Someone> " + line, pattern), null);
  const receipt = receiptPattern(
    "^To %nickname%: %message%$",
    "PlayerName",
    "Код: 123456.",
  );
  assert.ok(receipt.test("To PlayerName: Код: 123456."));
  assert.ok(!receipt.test("To OtherName: Код: 123456."));
  assert.ok(!receipt.test("To PlayerName: Код: 123456!"));
  assert.equal(receiptPattern(null, "PlayerName", "code"), null);
});
test("vanilla incoming whispers are accepted by their server translation, never by public-chat text", () => {
  const pattern =
    /^\s*\[?(?<sender>[A-Za-z0-9_]{3,16})\]?\s+whispers\s+to\s+you:\s*(?<message>[^\r\n]+)$/u;
  const line = "PlayerName whispers to you: $bal";
  assert.deepEqual(
    parsePrivate(line, pattern, "chat", {
      translate: "commands.message.display.incoming",
    }),
    { sender: "PlayerName", message: "$bal" },
  );
  assert.equal(
    parsePrivate(line, pattern, "chat", { translate: "chat.type.text" }),
    null,
  );
  assert.equal(
    parsePrivate(line, pattern, "chat", {
      translate: "commands.message.display.outgoing",
    }),
    null,
  );
  assert.equal(
    parsePrivate(line, pattern, "chat", {
      text: line,
      extra: [{ translate: "commands.message.display.incoming" }],
    }),
    null,
  );
});

test("vanilla outgoing receipts acknowledge the exact code and recipient without trusting public chat", () => {
  const success = receiptPattern(
    String.raw`^\s*You\s+whisper\s+to\s+\[?%nickname%\]?:\s*%message%\s*$`,
    "playername",
    "Код подтверждения Banking System: 482913",
  );
  const failure = /^\s*No\s+player\s+was\s+found[.!]?\s*$/iu;
  const line =
    "You whisper to PlayerName: Код подтверждения Banking System: 482913";
  const outgoing = { translate: "commands.message.display.outgoing" };
  assert.equal(
    deliveryResult(line, "chat", outgoing, success, failure),
    "delivered",
  );
  assert.equal(
    deliveryResult(line, "system", {}, success, failure),
    "delivered",
  );
  assert.equal(
    deliveryResult(
      line,
      "chat",
      { translate: "chat.type.text" },
      success,
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult(
      line,
      "chat",
      { translate: "commands.message.display.incoming" },
      success,
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult(
      line.replace("482913", "482914"),
      "chat",
      outgoing,
      success,
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult(
      line.replace("PlayerName", "OtherPlayer"),
      "chat",
      outgoing,
      success,
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult("No player was found", "system", {}, success, failure),
    "failed",
  );
});

test("selection excludes busy, stale and cross-dimension observations", () => {
  const base = {
    connected: true,
    busy: false,
    age_ms: 0,
    dimension: "overworld",
    player_dimension: "overworld",
  };
  const devices = [
    { ...base, id: "one", distance: 10 },
    { ...base, id: "two", distance: 2 },
    { ...base, id: "busy", distance: 1, busy: true },
    { ...base, id: "stale", distance: 0, age_ms: 4000 },
    { ...base, id: "dimension", distance: 0, player_dimension: "nether" },
  ];
  assert.equal(nearest(devices, 15).id, "two");
  assert.equal(nearest(devices, 1), undefined);
  assert.equal(selectionReason(devices[1], 15), null);
  assert.equal(selectionReason(devices[2], 15), "busy");
  assert.equal(selectionReason(devices[3], 15), "stale_or_unseen");
  assert.equal(selectionReason(devices[4], 15), "dimension_mismatch");
  assert.equal(selectionReason(devices[0], 1), "out_of_range");
  assert.equal(
    selectionReason({ ...base, connected: false }, 15),
    "disconnected",
  );
});
test("both inventory deltas must match exactly, including partial movement", () => {
  const proof = {
    inventory_before: 32,
    container_before: 0,
    inventory_after: 27,
    container_after: 5,
  };
  assert.ok(exactMovement("withdraw", 5, proof));
  assert.ok(!exactMovement("withdraw", 5, { ...proof, container_after: 4 }));
  assert.ok(!exactMovement("deposit", 5, proof));
});
test("physical-intent journal survives a new process object and preserves unknown outcomes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "horizon-atm-"));
  try {
    const id = randomUUID(),
      journal = new Journal(directory);
    await journal.init();
    await journal.write(id, {
      status: "PROCESSING",
      evidence: { inventory_before: 32 },
    });
    const recovered = await new Journal(directory).pending();
    assert.equal(recovered[0].status, "PROCESSING");
    assert.equal(recovered[0].id, id);
    await journal.write(id, { status: "COMPLETED" });
    assert.deepEqual(await journal.pending(), []);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
