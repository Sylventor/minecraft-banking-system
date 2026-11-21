import { readFile } from "node:fs/promises";

export async function loadConfig(path) {
  const c = JSON.parse(await readFile(path, "utf8"));
  if (
    c.hpay_protection_seconds !== undefined &&
    (!Number.isInteger(c.hpay_protection_seconds) ||
      c.hpay_protection_seconds < 1 ||
      c.hpay_protection_seconds > 60)
  )
    throw new Error(
      "hpay_protection_seconds должен быть целым числом от 1 до 60",
    );
  if (c.debug_messages !== undefined && typeof c.debug_messages !== "boolean")
    throw new Error("debug_messages должен быть true или false");
  if (
    !c.host ||
    !Number.isInteger(c.port) ||
    c.port < 1 ||
    c.port > 65535 ||
    c.currency_item !== "ancient_debris"
  )
    throw new Error("Задайте host, port и currency_item=ancient_debris");
  if (
    !Number.isInteger(c.max_operation_amount) ||
    c.max_operation_amount < 1 ||
    c.max_operation_amount > 2304 ||
    !(c.player_distance > 0 && c.player_distance <= 32)
  )
    throw new Error("Некорректные лимиты банкомата");
  const bots = c.bots.filter((x) => x.enabled);
  if (bots.filter((x) => x.master).length !== 1)
    throw new Error("Нужен ровно один включённый мастер");
  const ids = new Set(),
    names = new Set();
  for (const b of bots) {
    if (
      !/^[A-Za-z0-9_-]{1,32}$/.test(b.id) ||
      !/^[A-Za-z0-9_]{3,16}$/.test(b.username) ||
      ids.has(b.id) ||
      names.has(b.username.toLowerCase())
    )
      throw new Error(
        "Идентификаторы и ники банкоматов должны быть корректными и уникальными",
      );
    ids.add(b.id);
    names.add(b.username.toLowerCase());
    if (
      !["offline", "microsoft"].includes(b.auth) ||
      !b.container?.dimension ||
      !["x", "y", "z"].every((k) => Number.isInteger(b.container[k]))
    )
      throw new Error(
        "Задайте авторизацию и точные координаты контейнера каждого банкомата",
      );
    if (b.login_password_env && !process.env[b.login_password_env])
      throw new Error("Не задан пароль /login для " + b.id);
  }
  for (let i = 0; i < bots.length; i++)
    for (let j = i + 1; j < bots.length; j++) {
      const a = bots[i].container,
        b = bots[j].container;
      if (
        a.dimension === b.dimension &&
        a.y === b.y &&
        Math.abs(a.x - b.x) + Math.abs(a.z - b.z) <= 1
      )
        throw new Error(
          "Банкоматы должны использовать разные, несмежные контейнеры",
        );
    }
  for (const key of [
    "incoming_pattern",
    "failure_pattern",
    "login_success_pattern",
  ]) {
    if (typeof c.chat?.[key] !== "string" || c.chat[key].length > 1500)
      throw new Error("Некорректный формат сообщений");
    new RegExp(c.chat[key], "u");
  }
  if (
    !c.chat.incoming_pattern.startsWith("^") ||
    !c.chat.incoming_pattern.endsWith("$")
  )
    throw new Error("Шаблон входящих сообщений должен быть полным");
  if (
    c.chat.delivery_pattern &&
    (!c.chat.delivery_pattern.startsWith("^") ||
      !c.chat.delivery_pattern.endsWith("$") ||
      !c.chat.delivery_pattern.includes("%nickname%") ||
      !c.chat.delivery_pattern.includes("%message%"))
  )
    throw new Error(
      "Подтверждение доставки должно точно включать %nickname% и %message%",
    );
  c.bots = bots;
  return c;
}
