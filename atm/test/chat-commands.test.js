import test from "node:test";
import assert from "node:assert/strict";
import { ChatCommands } from "../src/chat-commands.js";
import { parseCommand } from "../src/protocol.js";
import { helpMessages, previewMessages } from "../src/messages.js";

function fixture(
  permissions = { can_test: true, can_collect: true },
  wait = async () => {},
) {
  const sent = [],
    calls = [],
    pauses = [];
  const master = {
    definition: { username: "ConfiguredBot123" },
    identity: () => ({ uuid: "00000000-0000-4000-8000-000000000001" }),
    message: async (sender, message, receipt) => {
      assert.equal(sender, "PlayerName");
      assert.ok(("<br>" + message).length <= 240);
      assert.equal(receipt, undefined);
      sent.push(message);
    },
  };
  const coordinator = {
    master: () => master,
    clients: new Map([["atm-1", master]]),
    stopping: false,
    guard: () => {},
    post: async (path, payload) => {
      // No mutations, purchases, transfers, reservations or inventory access.
      assert.equal(path, "capabilities");
      calls.push(payload);
      return permissions;
    },
  };
  const handler = new ChatCommands(
    coordinator,
    { max_operation_amount: 64 },
    async (delay) => {
      pauses.push(delay);
      await wait(delay);
    },
  );
  return { handler, coordinator, master, sent, calls, pauses };
}

test("help uses full PM commands, contains only a website click and hides collection without its grant", async () => {
  const f = fixture({ can_test: false, can_collect: false });
  await f.handler.handle("PlayerName", parseCommand("$help"));
  assert.equal(f.calls.length, 1);
  assert.ok(!f.sent.join("").includes("$clear-atm"));
  assert.ok(!f.sent.join("").includes("$test"));
  for (const message of f.sent) {
    assert.ok(!message.includes("suggest_command"));
    for (const match of message.matchAll(/\[([^\]]+)\]/g))
      if (match[1].includes("$"))
        assert.match(match[1], /^\/msg ConfiguredBot123 \$/);
  }
  assert.match(
    f.sent.join(""),
    /<click:open_url:'http:\/\/localhost:8080'>/,
  );
  for (const can_collect of [false, true]) {
    const text = helpMessages("ConfiguredBot123", {
      can_collect,
      can_test: true,
    }).join("");
    assert.equal(text.includes("$clear-atm"), can_collect);
    assert.ok(text.includes("[/msg ConfiguredBot123 $test]"));
    assert.ok(text.includes("[/msg ConfiguredBot123 $test stop]"));
  }
});

test("regular players and failed permission checks cannot start a preview; public help stays available", async () => {
  const f = fixture({ can_test: false, can_collect: false });
  await assert.rejects(
    f.handler.handle("PlayerName", parseCommand("test")),
    /Нет права/,
  );
  assert.equal(f.sent.length, 0);
  f.coordinator.post = async () => {
    throw new Error("Unavailable");
  };
  await assert.rejects(
    f.handler.handle("PlayerName", parseCommand("$test")),
    /Unavailable/,
  );
  assert.equal(f.sent.length, 0);
  await f.handler.handle("PlayerName", parseCommand("$help"));
  assert.ok(!f.sent.join("").includes("$clear-atm"));
  assert.ok(!f.sent.join("").includes("$test"));
});

test("preview renders the real message catalogue with pauses and no financial requests or active purchase buttons", async () => {
  const f = fixture();
  assert.equal(
    await f.handler.handle("PlayerName", parseCommand("$test")),
    true,
  );
  const examples = previewMessages("ConfiguredBot123");
  assert.deepEqual(f.sent.slice(1, -1), examples);
  assert.equal(f.calls.length, 1);
  assert.equal(f.pauses.length, examples.length);
  assert.ok(f.pauses.every((delay) => delay >= 1500));
  assert.match(f.sent[0], /Тест · Начало/);
  assert.match(f.sent.at(-1), /Тест · Завершён/);
  for (const message of examples) {
    assert.ok(!message.includes("run_command"));
    for (const action of message.matchAll(/<click:suggest_command:'([^']+)'>/g))
      assert.equal(action[1], "/msg ConfiguredBot123 $help");
  }
  await assert.rejects(
    f.handler.handle("PlayerName", parseCommand("$test")),
    /30 секунд/,
  );
});

test("a preview cannot overlap and can be stopped without waiting for all examples", async () => {
  let resume, reached;
  const paused = new Promise((resolve) => {
    reached = resolve;
  });
  const gate = new Promise((resolve) => {
    resume = resolve;
  });
  const f = fixture(undefined, async () => {
    reached();
    await gate;
  });
  const running = f.handler.handle("PlayerName", parseCommand("$test"));
  await paused;
  await assert.rejects(
    f.handler.handle("PlayerName", parseCommand("$test")),
    /уже запущена/,
  );
  await f.handler.handle("PlayerName", parseCommand("$test stop"));
  resume();
  await running;
  assert.equal(f.pauses.length, 1);
  assert.match(f.sent.at(-1), /Тест · Остановлен/);
  assert.equal(f.handler.running, null);
});
