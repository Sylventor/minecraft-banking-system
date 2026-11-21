import { createHash } from "node:crypto";

export function stripColors(text) {
  return text.replace(/§[0-9a-fk-orx]/gi, "");
}
const whisperTranslations = new Map([
  ["commands.message.display.incoming", "incoming"],
  ["%s whispers to you: %s", "incoming"],
  ["commands.message.display.outgoing", "outgoing"],
  ["You whisper to %s: %s", "outgoing"],
]);
export function whisperDirection(message) {
  // fromNetwork() may preserve the registry's literal formatString in translate
  // instead of a localization key. Accept only these exact server-selected types.
  return whisperTranslations.get(message?.translate) ?? null;
}
export function parsePrivate(text, pattern, position = "system", jsonMessage) {
  // Never reinterpret ordinary player chat as a server-generated private message.
  // Modern vanilla /msg uses the playerChat transport too. Its top-level
  // translation is chosen by the server's chat-type registry, not by chat text.
  if (
    position !== "system" &&
    !(position === "chat" && whisperDirection(jsonMessage) === "incoming")
  )
    return null;
  const match = stripColors(text).match(pattern);
  const sender = match?.groups?.sender,
    message = match?.groups?.message?.trim();
  return sender &&
    /^[A-Za-z0-9_]{3,16}$/.test(sender) &&
    message &&
    message.length <= 256
    ? { sender, message }
    : null;
}
export function parseCommand(text) {
  const parts = text.trim().split(/\s+/),
    command = parts[0].replace(/^\$/, "").toLowerCase();
  if (
    command === "test" &&
    (parts.length === 1 ||
      (parts.length === 2 && parts[1].toLowerCase() === "stop"))
  )
    return { kind: "test", stop: parts.length === 2 };
  if (parts.length === 1 && ["payhelp", "cancel"].includes(command))
    return { kind: command };
  if (
    parts.length === 2 &&
    command === "pay" &&
    /^[1-9]\d{0,18}$/.test(parts[1])
  )
    return { kind: "pay", product: parts[1] };
  if (
    parts.length === 1 &&
    ["help", "bal", "balance", "clear-atm"].includes(command)
  )
    return { kind: command === "balance" ? "bal" : command };
  if (
    parts.length === 2 &&
    /^(with|withdraw|dep|deposit)$/.test(command) &&
    /^[1-9]\d{0,3}$/.test(parts[1])
  )
    return {
      kind: command.startsWith("with") ? "withdraw" : "deposit",
      quantity: Number(parts[1]),
    };
  if (
    parts.length === 3 &&
    command === "transaction" &&
    /^[A-Za-z0-9_]{3,16}$/.test(parts[1]) &&
    /^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$/.test(parts[2]) &&
    !/^0(?:\.0{1,2})?$/.test(parts[2])
  )
    return { kind: "transfer", recipient: parts[1], amount: parts[2] }; // Keep money decimal, never float.
  throw new Error("Неизвестная команда или сумма. Используйте [$help].");
}
export function receiptPattern(template, nickname, message) {
  if (!template) return null;
  const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    template
      .replaceAll("%nickname%", escape(nickname))
      .replaceAll("%message%", escape(message)),
    "iu",
  );
}
export function deliveryResult(text, position, jsonMessage, success, failure) {
  const system = position === "system";
  const outgoing =
    position === "chat" && whisperDirection(jsonMessage) === "outgoing";
  if (!system && !outgoing) return null;
  const plain = stripColors(text);
  if (success.test(plain)) return "delivered";
  if (system && failure.test(plain)) return "failed";
  return null;
}
export function exactMovement(kind, quantity, proof) {
  const delta = kind === "deposit" ? quantity : -quantity;
  return (
    proof.inventory_after - proof.inventory_before === delta &&
    proof.container_after - proof.container_before === -delta
  );
}
export function selectionReason(candidate, maximum) {
  if (!candidate.connected) return "disconnected";
  if (candidate.busy) return "busy";
  if (!(candidate.age_ms >= 0 && candidate.age_ms <= 3000))
    return "stale_or_unseen";
  if (candidate.dimension !== candidate.player_dimension)
    return "dimension_mismatch";
  if (!Number.isFinite(candidate.distance) || candidate.distance > maximum)
    return "out_of_range";
  return null;
}
export function nearest(candidates, maximum) {
  return candidates
    .filter((x) => selectionReason(x, maximum) === null)
    .sort((a, b) => a.distance - b.distance || a.id.localeCompare(b.id))[0];
}
export const commandHash = (sender, message) =>
  createHash("sha256")
    .update(sender.toLowerCase() + ":" + message)
    .digest("hex");
