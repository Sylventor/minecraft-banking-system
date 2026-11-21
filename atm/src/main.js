import { createServer } from "node:http";
import { existsSync } from "node:fs";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";
import { loadConfig } from "./config.js";
import { Journal } from "./journal.js";
import { AtmClient } from "./client.js";
import { HPayChat } from "./hpay.js";
import { ChatCommands } from "./chat-commands.js";
import {
  alreadyCompletedMessage,
  balanceMessage,
  errorMessage,
  linkMessage,
  physicalMessage,
  transferMessage,
} from "./messages.js";
import {
  parseCommand,
  commandHash,
  nearest,
  selectionReason,
} from "./protocol.js";
import { deliveryFailureReason } from "./diagnostics.js";

if (
  process.env.ATM_BOT_SECRETS_FILE &&
  existsSync(process.env.ATM_BOT_SECRETS_FILE)
)
  process.loadEnvFile(process.env.ATM_BOT_SECRETS_FILE);
const config = await loadConfig(
  process.env.ATM_CONFIG_FILE || "/config/config.json",
);
const token = process.env.ATM_SERVICE_TOKEN || "";
if (token.length < 32)
  throw new Error("Задайте отдельный ATM_SERVICE_TOKEN длиной от 32 символов");
const identityMode = process.env.ATM_IDENTITY_MODE || "online-mode";
if (
  !["online-mode", "server-auth"].includes(identityMode) ||
  (identityMode === "server-auth" && config.trusted_server_auth !== true)
)
  throw new Error(
    "Offline-mode требует явного подтверждения trusted_server_auth и серверной защиты игровых аккаунтов",
  );
process.env.ATM_STATE_DIR ||= "/state";
config.identity_mode = identityMode;
if (
  identityMode === "online-mode" &&
  config.bots.some((bot) => bot.auth !== "microsoft")
)
  throw new Error(
    "Для online-mode требуется auth=microsoft. Для защищённого offline-сервера явно настройте server-auth.",
  );

class Coordinator {
  constructor() {
    this.instance = randomUUID();
    this.clients = new Map();
    this.cooldowns = new Map();
    this.dedup = new Map();
    this.blocked = new Set();
    this.lastLease = 0;
    this.stopping = false;
    this.journal = new Journal(process.env.ATM_STATE_DIR + "/journal");
    this.hpay = new HPayChat(this, config.hpay_protection_seconds ?? 3);
    this.chat = new ChatCommands(this, config);
  }
  log(event, device = "", details = {}) {
    console.log(
      JSON.stringify({
        time: new Date().toISOString(),
        event,
        device,
        instance: this.instance,
        ...details,
      }),
    );
  }
  async post(path, payload) {
    const response = await fetch(
      (process.env.HORIZON_API_URL || "http://backend:8000/api/v1") +
        "/internal/atm/" +
        path,
      {
        method: "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ...payload, instance_id: this.instance }),
        signal: AbortSignal.timeout(12000),
      },
    );
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.error === "string"
          ? data.error
          : "Банковский API недоступен",
      );
    return data;
  }
  guard() {
    if (this.stopping || Date.now() - this.lastLease > 15000)
      throw new Error("Нет действующей связи с координатором банка");
  }
  async heartbeat() {
    if (this.heartbeating) return this.heartbeating;
    this.heartbeating = this.post("heartbeat", {
      devices: [...this.clients.values()].map((c) => c.snapshot()),
    })
      .then((data) => {
        this.lastLease = Date.now();
        this.blocked = new Set(data.blocked);
      })
      .finally(() => {
        this.heartbeating = null;
      });
    return this.heartbeating;
  }
  async minimizePrivateData() {
    if (this.minimizing) return;
    this.minimizing = true;
    try {
      const response = await fetch(
        (process.env.HORIZON_API_URL || "http://backend:8000/api/v1") +
          "/internal/atm/privacy/tasks",
        {
          headers: { Authorization: "Bearer " + token },
          signal: AbortSignal.timeout(12000),
        },
      );
      if (!response.ok) return;
      const data = await response.json();
      for (const task of data.items) {
        // Reconcile stale local intent with core before touching a physical fence.
        for (const record of await this.journal.pending()) {
          if (record.status === "REQUESTED") continue;
          if (
            record.user_id !== task.user_id &&
            !task.nicknames.some(
              (name) =>
                name.toLowerCase() ===
                String(record.nickname || "").toLowerCase(),
            )
          )
            continue;
          const state = await this.operation(record.id, "get").catch(
            () => null,
          );
          if (state && ["COMPLETED", "FAILED"].includes(state.status))
            await this.journal.write(record.id, state);
        }
        const result = await this.journal.minimize(task);
        const ack = await fetch(
          (process.env.HORIZON_API_URL || "http://backend:8000/api/v1") +
            "/internal/atm/privacy/tasks/" +
            task.case_id,
          {
            method: "POST",
            headers: {
              Authorization: "Bearer " + token,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ blocked: result.blocked }),
            signal: AbortSignal.timeout(12000),
          },
        );
        if (!ack.ok) break;
      }
      await this.journal.minimize();
    } catch {
      this.log("privacy.maintenance_retry");
    } finally {
      this.minimizing = false;
    }
  }
  operation(id, action, body = {}) {
    if (body.evidence?.inventory_before === undefined) delete body.evidence;
    return this.post("operations/" + id + "/" + action, body);
  }
  master() {
    return [...this.clients.values()].find((c) => c.definition.master);
  }
  async command(sender, message, delivery) {
    if (/^\s*\$?(pay|payhelp|cancel)(?:\s|$)/i.test(message)) {
      try {
        await this.hpay.command(sender, parseCommand(message), delivery);
      } catch (error) {
        await this.master()
          .message(
            sender,
            errorMessage(
              error.message,
              this.master().definition.username,
              true,
            ),
          )
          .catch(() => {});
      }
      return;
    }
    const master = this.master(),
      now = Date.now(),
      hash = commandHash(sender, message);
    if (
      (this.dedup.get(hash) || 0) > now ||
      ((this.cooldowns.get(sender.toLowerCase()) || 0) > now &&
        !/^\s*\$?test\s+stop\s*$/i.test(message))
    )
      return;
    for (const [key, until] of this.dedup)
      if (until < now) this.dedup.delete(key);
    for (const [key, until] of this.cooldowns)
      if (until < now) this.cooldowns.delete(key);
    this.dedup.set(hash, now + 5000);
    this.cooldowns.set(
      sender.toLowerCase(),
      now + Math.max(3000, config.cooldown_ms || 5000),
    );
    try {
      this.guard();
      const command = parseCommand(message);
      if (await this.chat.handle(sender, command)) return;
      const identity =
        master.identity(sender) ||
        [...this.clients.values()].map((c) => c.identity(sender)).find(Boolean);
      if (!identity)
        throw new Error(
          "Не удалось подтвердить игровую личность. Убедитесь, что вы вошли на сервер.",
        );
      const common = { nickname: sender, player_uuid: identity.uuid };
      if (command.kind === "bal") {
        const value = await this.post("balance", common);
        await master.message(
          sender,
          balanceMessage(value.balance, value.available),
        );
        return;
      }
      const key = randomUUID();
      await this.journal.write(key, {
        status: "REQUESTED",
        nickname: sender,
        command,
      });
      if (command.kind === "transfer") {
        const result = await this.post("transfers", {
          ...common,
          request_key: key,
          recipient: command.recipient,
          amount: command.amount,
        });
        await this.journal.write(key, { status: "COMPLETED", ...result });
        await master.message(
          sender,
          transferMessage(command.recipient, command.amount),
        );
        return;
      }
      await this.heartbeat();
      const candidates = [...this.clients.values()].map((c) => {
        const snapshot = c.snapshot(),
          p = snapshot.observations[sender.toLowerCase()];
        return {
          ...snapshot,
          busy: c.busy || this.blocked.has(c.definition.id),
          age_ms: p?.age_ms ?? Infinity,
          player_dimension: p?.dimension,
          distance: p
            ? Math.hypot(
                p.x - snapshot.position.x,
                p.y - snapshot.position.y,
                p.z - snapshot.position.z,
              )
            : Infinity,
        };
      });
      const selected = nearest(candidates, config.player_distance);
      master.debug("command.selection", {
        selected: selected?.id ?? null,
        maximum_distance: config.player_distance,
        candidates: candidates.map((candidate) => ({
          id: candidate.id,
          connected: candidate.connected,
          busy: candidate.busy,
          blocked: this.blocked.has(candidate.id),
          age_ms: Number.isFinite(candidate.age_ms) ? candidate.age_ms : null,
          distance: Number.isFinite(candidate.distance)
            ? candidate.distance
            : null,
          dimension: candidate.dimension,
          player_dimension: candidate.player_dimension ?? null,
          reason: selectionReason(candidate, config.player_distance),
        })),
      });
      if (!selected)
        throw new Error(
          nearest(
            candidates.map((candidate) => ({ ...candidate, busy: false })),
            config.player_distance,
          )
            ? "Банкомат рядом занят или ожидает проверки предыдущей операции. Проверьте его статус в админке."
            : "Рядом нет подключённого банкомата, который видит вас. Проверьте подключение ATM и подойдите ближе.",
        );
      const collection = command.kind === "clear-atm";
      const quantity = collection
        ? await this.clients
            .get(selected.id)
            .collectionQuantity(sender, identity.uuid)
        : command.quantity;
      if (!quantity || (!collection && quantity > config.max_operation_amount))
        throw new Error(
          "Количество должно быть от 1 до " +
            config.max_operation_amount +
            " ДБ.",
        );
      // Opening the collection container can take time. Refresh the inventory
      // and proximity observations before core validates the prepared amount.
      if (collection) await this.heartbeat();
      const operation = await this.post("operations", {
        ...common,
        request_key: key,
        kind: collection ? "collection" : command.kind,
        quantity,
      });
      if (operation.status === "COMPLETED") {
        await master.message(sender, alreadyCompletedMessage());
        return;
      }
      this.blocked.add(operation.device_id);
      const client = this.clients.get(operation.device_id);
      if (!client) throw new Error("Выбранный банкомат отключён");
      await client.execute(operation);
      await this.journal.write(key, {
        status: "COMPLETED",
        operation_id: operation.id,
      });
      await master.message(
        sender,
        physicalMessage(
          collection ? "collection" : operation.kind,
          operation.quantity,
        ),
      );
    } catch (error) {
      this.log("command.rejected");
      master?.debug("command.rejected", { reason: String(error.message) });
      await master
        ?.message(
          sender,
          errorMessage(error.message, master.definition.username),
        )
        .catch(() => {});
    }
  }
}

const coordinator = new Coordinator();
await coordinator.journal.init();
// Never reconnect Minecraft clients before acquiring the single coordinator lease.
while (!coordinator.stopping) {
  try {
    await coordinator.post("lease", {
      max_amount: config.max_operation_amount,
      player_distance: config.player_distance,
    });
    coordinator.lastLease = Date.now();
    break;
  } catch {
    coordinator.log("lease.waiting");
    await sleep(5000);
  }
}
for (const definition of config.bots) {
  const client = new AtmClient(config, definition, coordinator);
  coordinator.clients.set(definition.id, client);
  client.connect();
}
const pending = await coordinator.journal.pending();
if (pending.length) coordinator.log("journal.requires_reconciliation"); // The core fences and retains old physical operations.

const http = createServer(async (request, response) => {
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  const supplied = Buffer.from(request.headers.authorization || ""),
    expected = Buffer.from("Bearer " + token);
  if (
    supplied.length !== expected.length ||
    !timingSafeEqual(supplied, expected)
  ) {
    response.writeHead(401).end("{}");
    return;
  }
  let stage = "request",
    challengeId = null;
  try {
    coordinator.guard();
    if (request.method === "GET" && request.url === "/health") {
      response.end(
        JSON.stringify({ ready: Boolean(coordinator.master()?.ready) }),
      );
      return;
    }
    if (request.method !== "POST" || request.url !== "/deliver") {
      response.writeHead(404).end("{}");
      return;
    }
    let raw = "";
    for await (const part of request) {
      raw += part;
      if (Buffer.byteLength(raw) > 2048) throw new Error("body");
    }
    const body = JSON.parse(raw),
      master = coordinator.master();
    if (
      !/^[A-Za-z0-9_]{3,16}$/.test(body.nickname) ||
      !/^\d{6}$/.test(body.code) ||
      !/^[0-9a-f-]{36}$/.test(body.challenge_id)
    )
      throw new Error("body");
    challengeId = body.challenge_id;
    stage = "presence";
    const identity = master.identity(body.nickname);
    master.debug("delivery.identity", {
      challenge_id: challengeId,
      player: body.nickname,
      found: Boolean(identity),
      ready: master.ready,
      last_packet_age_ms: Date.now() - master.lastPacket,
      authentication: identityMode,
    });
    if (!identity) throw new Error("presence");
    stage = "send";
    await master.message(body.nickname, linkMessage(body.code), true);
    stage = "identity";
    const current = master.identity(body.nickname);
    master.debug("delivery.identity_after", {
      challenge_id: challengeId,
      found: Boolean(current),
      same_identity: current?.uuid === identity.uuid,
      last_packet_age_ms: Date.now() - master.lastPacket,
    });
    if (!current || current.uuid !== identity.uuid) throw new Error("identity");
    coordinator.log("delivery.confirmed", master.definition.id, {
      challenge_id: challengeId,
      authentication: identityMode,
    });
    response.end(
      JSON.stringify({
        delivered: true,
        player_uuid: identity.uuid,
        authentication: identityMode,
      }),
    );
  } catch (error) {
    const reason = deliveryFailureReason(stage, error);
    coordinator.log("delivery.failed", "", {
      challenge_id: challengeId,
      stage,
      reason,
    });
    response.writeHead(503).end(JSON.stringify({ delivered: false, reason }));
  }
});
http.listen(8090, "0.0.0.0");
const shutdown = () => {
  coordinator.stopping = true;
  http.close();
  for (const c of coordinator.clients.values()) c.stop();
  setTimeout(() => process.exit(0), 1500).unref();
};
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
let privacyMaintenance = null;
let lastPrivacyMaintenance = 0;
while (!coordinator.stopping) {
  // Maintenance must not delay the coordinator's heartbeat or physical fences.
  if (!privacyMaintenance && Date.now() - lastPrivacyMaintenance >= 60000) {
    lastPrivacyMaintenance = Date.now();
    privacyMaintenance = coordinator.minimizePrivateData().finally(() => {
      privacyMaintenance = null;
    });
  }
  try {
    await coordinator.heartbeat();
  } catch {
    coordinator.log("heartbeat.failed");
    if (Date.now() - coordinator.lastLease > 45000) {
      try {
        await coordinator.post("lease", {
          max_amount: config.max_operation_amount,
          player_distance: config.player_distance,
        });
        coordinator.lastLease = Date.now();
        coordinator.log("lease.recovered");
      } catch {
        /* Another coordinator may own the fence. No physical action is repeated. */
      }
    }
  }
  await sleep(5000);
}
