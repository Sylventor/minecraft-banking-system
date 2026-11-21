import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createRequire } from "node:module";
import { AtmClient } from "../src/client.js";
import { chatCommand, currencyIcon } from "../src/chat-format.js";
import {
  parsePrivate,
  deliveryResult,
  receiptPattern,
} from "../src/protocol.js";

const require = createRequire(import.meta.url);
const registry = require("prismarine-registry")("1.21.4");
const ChatMessage = require("prismarine-chat")(registry);
// Capture the actual registry format from the reported server. Exercise the
// same fromNetwork() factory Mineflayer uses, rather than constructing a key.
registry.chatFormattingById = {
  0: {
    formatString: "You whisper to %s: %s",
    parameters: ["target", "content"],
  },
  1: {
    formatString: "%s whispers to you: %s",
    parameters: ["sender", "content"],
  },
  2: { formatString: "<%s> %s", parameters: ["sender", "content"] },
};

test("server-registry literal whispers are recognized in both directions with exact receipt binding", () => {
  const text = "Код подтверждения Banking System: 482913";
  const outgoing = ChatMessage.fromNetwork(0, {
    target: { text: "DemoOwner" },
    content: { text },
  });
  assert.equal(outgoing.translate, "You whisper to %s: %s");
  const success = receiptPattern(
    String.raw`^You whisper to %nickname%: %message%$`,
    "DemoOwner",
    text,
  );
  const failure = /^No player was found$/u;
  assert.equal(
    deliveryResult(outgoing.toString(), "chat", outgoing, success, failure),
    "delivered",
  );
  assert.equal(
    deliveryResult(
      outgoing.toString(),
      "chat",
      outgoing,
      receiptPattern(
        String.raw`^You whisper to %nickname%: %message%$`,
        "DemoOwner",
        text.replace("482913", "482914"),
      ),
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult(
      outgoing.toString(),
      "chat",
      outgoing,
      receiptPattern(
        String.raw`^You whisper to %nickname%: %message%$`,
        "OtherPlayer",
        text,
      ),
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult(
      outgoing.toString(),
      "chat",
      { translate: "<%s> %s" },
      success,
      failure,
    ),
    null,
  );
  assert.equal(
    deliveryResult(
      outgoing.toString(),
      "chat",
      { translate: "chat.type.text", extra: [outgoing] },
      success,
      failure,
    ),
    null,
  );

  const incoming = ChatMessage.fromNetwork(1, {
    sender: { text: "DemoOwner" },
    content: { text: "$bal" },
  });
  const pattern =
    /^(?<sender>[A-Za-z0-9_]{3,16}) whispers to you: (?<message>[^\r\n]+)$/u;
  assert.deepEqual(
    parsePrivate(incoming.toString(), pattern, "chat", incoming),
    { sender: "DemoOwner", message: "$bal" },
  );
  assert.equal(
    parsePrivate(incoming.toString(), pattern, "chat", {
      translate: "<%s> %s",
    }),
    null,
  );
});

test("message() starts with one br tag and confirms rendered or literal receipts only from private chat", async () => {
  const text =
    "<br><gold>Banking System</gold><br>Код подтверждения: 482913<br>Сумма: 5,00 " +
    currencyIcon +
    "<br>" +
    chatCommand("BankingATM", "$help", "Помощь");
  const client = new AtmClient(
    {
      message_delay_ms: 300,
      chat: {
        delivery_pattern: String.raw`^You whisper to %nickname%: %message%$`,
        failure_pattern: "^No player was found$",
      },
    },
    { username: "BankingATM" },
    {},
  );
  const bot = new EventEmitter();
  client.bot = bot;
  client.ready = true;
  for (const [input, echoed] of [
    [
      text.slice(4).replaceAll("<br>", "\r\n"),
      {
        text: "\nBanking System\nКод подтверждения: 482913\nСумма: 5,00 ",
        extra: [
          {
            type: "object",
            atlas: "minecraft:blocks",
            sprite: "minecraft:block/ancient_debris_side",
          },
          {
            text: "\n[Помощь]",
            clickEvent: {
              action: "suggest_command",
              value: "/msg BankingATM $help",
            },
          },
        ],
      },
    ],
    [text, { text }],
  ]) {
    const outgoing = ChatMessage.fromNetwork(0, {
      target: { text: "PlayerName" },
      content: echoed,
    });
    bot.chat = (command) => {
      assert.equal(command, "/msg playername " + text);
      bot.emit("messagestr", outgoing.toString(), "chat", {
        translate: "chat.type.text",
      });
      assert.equal(bot.listenerCount("messagestr"), 1);
      bot.emit(
        "messagestr",
        outgoing.toString().replace("5,00", "5,01"),
        "chat",
        outgoing,
      );
      assert.equal(bot.listenerCount("messagestr"), 1);
      bot.emit("messagestr", outgoing.toString(), "chat", outgoing);
    };
    await client.message("playername", input, true);
    assert.equal(bot.listenerCount("messagestr"), 0);
  }
});
