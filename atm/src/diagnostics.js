// Diagnostics never serializes raw chat components or authentication headers.
export function redactDiagnostic(value, secrets = []) {
  let text = String(value)
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/§[0-9a-fk-orx]/gi, "");
  for (const secret of secrets) {
    if (typeof secret === "string" && secret.length)
      text = text.split(secret).join("<secret>");
  }
  return text
    .replace(/\/(?:login|l|register|reg)\b[^\r\n]*/gi, "/login <secret>")
    .replace(/Bearer\s+\S+/gi, "Bearer <secret>")
    .replace(/\b\d{6}\b/g, "<code>")
    .replace(/\b[A-Za-z0-9_-]{40,}\b/g, "<secret>")
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "")
    .slice(0, 1000);
}

export function makeDebugLogger(enabled, secrets, emit) {
  return (event, details = {}) => {
    if (!enabled) return;
    const safe = JSON.parse(
      JSON.stringify(details, (key, value) => {
        if (/^(?:code|password|token|secret|authorization|cookie)$/i.test(key))
          return "<secret>";
        if (/^(?:text|recipient|player|nickname|player_uuid|sender)$/i.test(key))
          return "<private>";
        return typeof value === "string"
          ? redactDiagnostic(value, secrets)
          : value;
      }),
    );
    emit("debug." + event, safe);
  };
}

export function chatMetadata(text, position, message) {
  return {
    position: position ?? null,
    translate: message?.translate ?? null,
    component_keys: Object.keys(message?.json || {}).slice(0, 12),
    text,
  };
}

export function deliveryFailureReason(stage, error) {
  const known = {
    body: "invalid_request",
    presence: "player_unavailable",
    identity: "identity_changed",
    "Сервер не подтвердил доставку": "receipt_timeout",
    "Игрок не найден сервером": "player_not_found",
    "Мастер-бот недоступен": "master_unavailable",
    "Сервер не настроен для надёжного подтверждения доставки":
      "delivery_not_configured",
    "Нет действующей связи с координатором банка": "lease_unavailable",
  };
  return Object.hasOwn(known, error?.message)
    ? known[error.message]
    : stage === "send"
      ? "send_failed"
      : "delivery_unavailable";
}
