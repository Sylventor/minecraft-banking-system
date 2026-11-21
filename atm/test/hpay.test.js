import test from "node:test";
import assert from "node:assert/strict";
import { HPayChat } from "../src/hpay.js";
import { parseCommand } from "../src/protocol.js";
import { chatCommand, currencyIcon } from "../src/chat-format.js";

for (const longQuote of [false, true]) {
  test(`HPay ${longQuote ? "bracketed" : "clickable"} quote waits for delivery and confirms its exact id only once`, async (t) => {
    let now = 1_000;
    t.mock.method(Date, "now", () => now);
    const calls = [];
    let deliver;
    const prompt = new Promise((resolve) => {
      deliver = resolve;
    });
    const quote = {
      id: "quote-1",
      status: "pending",
      product_name: longQuote ? "x".repeat(80) : "Pickaxe",
      merchant_name: longQuote ? "y".repeat(80) : "Shop",
      price: longQuote ? "9999999999999999.99" : "5.00",
      expires_at: new Date(60_000).toISOString(),
    };
    const productId = longQuote ? "9223372036854775807" : "12345";
    const master = {
      identity: () => ({ uuid: "player-1" }),
      definition: { username: "ConfiguredBot123" },
      message: async (_sender, message, receipt) => {
        assert.ok(("<br>" + message).length <= 240);
        assert.ok(message.includes(currencyIcon));
        if (receipt) {
          assert.ok(
            message.includes("/msg ConfiguredBot123 $pay " + productId),
          );
          if (longQuote) {
            assert.ok(
              message.includes(
                "[/msg ConfiguredBot123 $pay " + productId + "]",
              ),
            );
            assert.ok(!message.includes("<click:"));
          } else {
            assert.ok(
              message.includes(
                chatCommand(
                  "ConfiguredBot123",
                  "$pay " + productId,
                  "Оплатить",
                ),
              ),
            );
            assert.ok(message.includes("→ Enter"));
          }
          await prompt;
        }
      },
    };
    const coordinator = {
      master: () => master,
      guard: () => {},
      post: async (path, body) => {
        calls.push([path, body]);
        return path === "hpay/quote" && calls.length === 1
          ? quote
          : { ...quote, status: "completed" };
      },
    };
    const handler = new HPayChat(coordinator, 3);
    const command = parseCommand("$pay " + productId);
    const firstPacket = { id: "first-packet", sender: "player-1" };
    const initial = handler.command("Player", command, firstPacket);
    await handler.command("Player", command);
    assert.equal(calls.length, 1);
    deliver();
    await initial;
    await handler.command("Player", command);
    assert.equal(calls.length, 1);
    now += 800;
    await handler.command("Player", command, firstPacket);
    assert.equal(calls.length, 1);
    await Promise.all([
      handler.command("Player", command),
      handler.command("Player", command),
    ]);
    assert.equal(calls.length, 2);
    assert.equal(calls[1][0], "hpay/confirm");
    assert.equal(calls[1][1].purchase_id, quote.id);
    now += 800;
    await handler.command("Player", command);
    assert.equal(calls[2][0], "hpay/quote");
    assert.equal(calls[2][1].purchase_id, undefined);
  });
}
