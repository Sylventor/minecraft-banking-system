import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chatMetadata,
  deliveryFailureReason,
  makeDebugLogger,
  redactDiagnostic,
} from "../src/diagnostics.js";

test("diagnostics hides codes, known passwords, bearer tokens and terminal controls", () => {
  const password = "private-game-password",
    token = "private-service-token-" + "x".repeat(40);
  const source = `\u001b[31mКод подтверждения Banking System: 482913\n/login ${password}\nBearer ${token}`;
  const result = redactDiagnostic(source, [password, token]);
  for (const secret of ["482913", password, token, "\u001b"])
    assert.ok(!result.includes(secret));
  assert.ok(result.includes("<code>"));
  assert.ok(!redactDiagnostic("Код: 48§a2913").includes("482913"));
  assert.equal(redactDiagnostic("Код: 48§a2913"), "Код: <code>");
});

test("debug is opt-in and only emits a scrubbed representation of chat", () => {
  const records = [];
  const emit = (event, details) => records.push({ event, details });
  makeDebugLogger(false, [], emit)("chat.received", { text: "code 482913" });
  assert.equal(records.length, 0);
  makeDebugLogger(
    true,
    ["password-example"],
    emit,
  )("chat.received", {
    ...chatMetadata(
      "You whisper to Player: Код: 482913 password-example",
      "chat",
      {
        translate: "commands.message.display.outgoing",
        json: { translate: "x", with: ["482913"] },
      },
    ),
    code: 482913,
    token: "private",
  });
  const raw = JSON.stringify(records);
  assert.ok(
    !raw.includes("482913") &&
      !raw.includes("password-example") &&
      !raw.includes('"private"'),
  );
  assert.equal(
    records[0].details.translate,
    "commands.message.display.outgoing",
  );
  assert.equal(records[0].details.position, "chat");
});

test("failure reasons are allowlisted rather than arbitrary exception contents", () => {
  assert.equal(
    deliveryFailureReason("send", new Error("constructor")),
    "send_failed",
  );
  assert.equal(
    deliveryFailureReason("send", new Error("Сервер не подтвердил доставку")),
    "receipt_timeout",
  );
  assert.equal(
    deliveryFailureReason("identity", new Error("identity")),
    "identity_changed",
  );
  assert.equal(
    deliveryFailureReason("send", new Error("private code 482913")),
    "send_failed",
  );
});
