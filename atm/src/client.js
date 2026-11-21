import mineflayer from "mineflayer";
import { Vec3 } from "vec3";
import { setTimeout as sleep } from "node:timers/promises";
import { createHash } from "node:crypto";
import { chatMetadata, makeDebugLogger } from "./diagnostics.js";
import { receiptText } from "./chat-format.js";
import {
  parsePrivate,
  receiptPattern,
  deliveryResult,
  stripColors,
  exactMovement,
} from "./protocol.js";

const dimension = (bot) =>
  String(bot.game?.dimension || "").replace(/^minecraft:/, "");
export class AtmClient {
  constructor(config, definition, coordinator) {
    this.config = config;
    this.definition = definition;
    this.coordinator = coordinator;
    this.ready = false;
    this.busy = false;
    this.error = "";
    this.queue = Promise.resolve();
    this.echoes = new Map();
    this.debug = makeDebugLogger(
      config.debug_messages === true,
      [
        process.env.ATM_SERVICE_TOKEN,
        ...(config.bots || [definition]).map(
          (bot) => process.env[bot.login_password_env],
        ),
      ],
      (event, details) => coordinator.log(event, definition.id, details),
    );
    this.debug("configuration", {
      identity_mode: config.identity_mode,
      incoming_pattern: config.chat?.incoming_pattern,
      delivery_pattern: config.chat?.delivery_pattern,
      failure_pattern: config.chat?.failure_pattern,
      native_whisper_receipts: true,
    });
  }
  connect() {
    if (this.coordinator.stopping) return;
    this.ready = false;
    const bot = (this.bot = mineflayer.createBot({
      host: this.config.host,
      port: this.config.port,
      username: this.definition.username,
      auth: this.definition.auth,
      version: this.config.version || false,
      profilesFolder: process.env.ATM_STATE_DIR + "/profiles",
      hideErrors: true,
    }));
    this.lastPacket = Date.now();
    this.onlineAuthenticated = false;
    let chatPacket;
    bot._client.on("packet", (packet, metadata, buffer) => {
      this.lastPacket = Date.now();
      if (metadata.name === "encryption_begin") this.onlineAuthenticated = true;
      chatPacket =
        metadata.name === "player_chat" &&
        packet.timestamp &&
        Buffer.isBuffer(buffer)
          ? {
              id: createHash("sha256").update(buffer).digest("hex"),
              sender: packet.senderUuid,
            }
          : undefined;
      if (chatPacket) {
        const received = chatPacket;
        queueMicrotask(() => {
          if (chatPacket === received) chatPacket = undefined;
        });
      }
    });
    bot.once("spawn", async () => {
      this.error = "";
      if (
        this.config.identity_mode === "online-mode" &&
        !this.onlineAuthenticated
      ) {
        this.error =
          "Сервер не подтвердил online-mode. Проверьте режим идентификации.";
        bot.quit();
        return;
      }
      const password = process.env[this.definition.login_password_env];
      if (password) {
        await sleep(500);
        if (this.bot === bot && !this.coordinator.stopping)
          bot.chat("/login " + password);
        this.loginTimer = setTimeout(() => {
          if (!this.ready) {
            this.error = "Сервер не подтвердил /login";
            bot.quit();
          }
        }, 30000);
      } else this.ready = true;
      this.coordinator.log("spawn", this.definition.id);
    });
    bot.on("messagestr", (text, position, jsonMessage) => {
      this.debug("chat.received", {
        ...chatMetadata(text, position, jsonMessage),
        ready: this.ready,
      });
      const plain = stripColors(text);
      if (
        position === "system" &&
        !this.ready &&
        new RegExp(this.config.chat.login_success_pattern, "u").test(plain)
      ) {
        this.ready = true;
        clearTimeout(this.loginTimer);
      }
      if (!this.definition.master || !this.ready) return;
      const incoming = parsePrivate(
        plain,
        new RegExp(this.config.chat.incoming_pattern, "u"),
        position,
        jsonMessage,
      );
      if (
        !incoming ||
        incoming.sender.toLowerCase() === this.definition.username.toLowerCase()
      )
        return;
      const key = incoming.sender.toLowerCase() + ":" + incoming.message;
      if ((this.echoes.get(key) || 0) > Date.now()) return;
      void this.coordinator
        .command(incoming.sender, incoming.message, chatPacket)
        .catch(() => this.coordinator.log("command.error", this.definition.id));
    });
    bot.on("death", () => {
      this.ready = false;
      this.error = "Бот погиб: проверьте сохранность дебрисов";
      bot.quit();
    });
    bot.on("error", () => {
      this.error = "Ошибка соединения с Minecraft";
      this.coordinator.log("connection.error", this.definition.id);
    });
    bot.once("end", () => {
      this.ready = false;
      clearTimeout(this.loginTimer);
      this.coordinator.log("disconnected", this.definition.id);
      this.reconnectTimer = setTimeout(
        () => this.connect(),
        Math.max(1000, this.config.reconnect_delay_ms || 5000),
      );
    });
  }
  identity(nickname) {
    if (
      this.config.identity_mode === "online-mode" &&
      !this.onlineAuthenticated
    )
      return null;
    if (!this.ready || !this.bot.entity || Date.now() - this.lastPacket > 3000)
      return null;
    const entity = Object.values(this.bot.entities).find(
      (e) =>
        e.type === "player" &&
        e.username?.toLowerCase() === nickname.toLowerCase(),
    );
    const tab = Object.values(this.bot.players).find(
      (p) => p.username?.toLowerCase() === nickname.toLowerCase(),
    );
    const raw = entity?.uuid || tab?.uuid;
    if (!raw) return null;
    const uuid = raw.includes("-")
      ? raw.toLowerCase()
      : raw
          .toLowerCase()
          .replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, "$1-$2-$3-$4-$5");
    if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(uuid))
      return null;
    return { uuid, entity, dimension: dimension(this.bot) };
  }
  observation(nickname) {
    const identity = this.identity(nickname);
    if (!identity?.entity || !this.bot.entities[identity.entity.id])
      return null;
    const p = identity.entity.position;
    return {
      uuid: identity.uuid,
      x: p.x,
      y: p.y,
      z: p.z,
      dimension: identity.dimension,
      age_ms: Date.now() - this.lastPacket,
    };
  }
  nearby(nickname, uuid) {
    const p = this.observation(nickname);
    return (
      p &&
      p.uuid === uuid &&
      p.dimension === this.definition.container.dimension &&
      this.bot.entity.position.distanceTo(new Vec3(p.x, p.y, p.z)) <=
        this.config.player_distance
    );
  }
  renamed(item) {
    return (
      item?.customName != null ||
      item?.nbt?.value?.display?.value?.Name != null ||
      item?.components?.some((component) =>
        [
          "custom_name",
          "minecraft:custom_name",
          "item_name",
          "minecraft:item_name",
        ].includes(component.type),
      ) === true
    );
  }
  requireUnnamedCurrency(container) {
    // Mineflayer transfers by item type, without filtering custom names. Refuse
    // mixed windows too, so a renamed stack can never be selected or merged.
    if (
      container.slots.some(
        (item) =>
          item?.name === this.config.currency_item && this.renamed(item),
      )
    )
      throw new Error(
        "Переименованные древние обломки не принимаются. Уберите их из контейнера и инвентаря банкомата",
      );
  }
  count(items) {
    return items
      .filter((x) => x?.name === this.config.currency_item && !this.renamed(x))
      .reduce((sum, x) => sum + x.count, 0);
  }
  capacity(slots) {
    return slots.reduce(
      (sum, item) =>
        sum +
        (!item
          ? 64
          : item.name === this.config.currency_item &&
              !this.renamed(item) &&
              !item.nbt
            ? Math.max(0, 64 - item.count)
            : 0),
      0,
    );
  }
  snapshot() {
    const bot = this.bot,
      connected = Boolean(
        this.ready && bot?.entity && Date.now() - this.lastPacket <= 3000,
      );
    const observations = {};
    if (connected)
      for (const entity of Object.values(bot.entities)) {
        if (!entity.username || entity.username === bot.username) continue;
        const p = this.observation(entity.username);
        if (
          p &&
          bot.entity.position.distanceTo(entity.position) <=
            this.config.player_distance
        )
          observations[entity.username.toLowerCase()] = p;
      }
    const slots = bot?.inventory?.slots.slice(9, 45) || [];
    return {
      id: this.definition.id,
      username: this.definition.username,
      master: this.definition.master,
      connected,
      dimension: connected ? dimension(bot) : "",
      position: connected
        ? {
            x: bot.entity.position.x,
            y: bot.entity.position.y,
            z: bot.entity.position.z,
          }
        : { x: 0, y: 0, z: 0 },
      observations,
      inventory: this.count(slots),
      free_space: this.capacity(slots),
      error: this.error,
      container: this.definition.container,
    };
  }
  async message(nickname, text, receipt = false) {
    text = text.replace(/\r\n|[\r\n]/g, "<br>");
    if (!text.startsWith("<br>")) text = "<br>" + text;
    if (!/^[A-Za-z0-9_]{3,16}$/.test(nickname) || text.length > 240)
      throw new Error("Некорректное сообщение");
    const task = async () => {
      if (!this.ready) throw new Error("Мастер-бот недоступен");
      for (const [key, expiry] of this.echoes)
        if (expiry < Date.now()) this.echoes.delete(key);
      this.echoes.set(nickname.toLowerCase() + ":" + text, Date.now() + 30000);
      let delivery;
      if (receipt) {
        const success = receiptPattern(
          this.config.chat.delivery_pattern ||
            (receipt === "hpay"
              ? "^You whisper to %nickname%: %message%$"
              : null),
          nickname,
          receiptText(text),
        );
        if (!success)
          throw new Error(
            "Сервер не настроен для надёжного подтверждения доставки",
          );
        this.debug("receipt.wait", {
          recipient: nickname,
          pattern: success.source,
          flags: success.flags,
        });
        delivery = new Promise((resolve, reject) => {
          let observed = 0;
          const listener = (line, position, jsonMessage) => {
            observed++;
            line = receiptText(line);
            const failure = new RegExp(this.config.chat.failure_pattern, "iu");
            const result = deliveryResult(
              line,
              position,
              jsonMessage,
              success,
              failure,
            );
            this.debug("receipt.observed", {
              ...chatMetadata(line, position, jsonMessage),
              pattern_matches: success.test(stripColors(line)),
              failure_matches: failure.test(stripColors(line)),
              result,
            });
            if (result === "delivered") {
              this.debug("receipt.confirmed", { recipient: nickname });
              cleanup();
              resolve();
            } else if (result === "failed") {
              cleanup();
              reject(new Error("Игрок не найден сервером"));
            }
          };
          const timer = setTimeout(() => {
            this.debug("receipt.timeout", {
              recipient: nickname,
              observed_messages: observed,
            });
            cleanup();
            reject(new Error("Сервер не подтвердил доставку"));
          }, 8000);
          const cleanup = () => {
            clearTimeout(timer);
            this.bot.removeListener("messagestr", listener);
          };
          this.bot.on("messagestr", listener);
        });
      }
      this.bot.chat("/msg " + nickname + " " + text);
      if (delivery) await delivery;
      await sleep(Math.max(300, this.config.message_delay_ms || 500));
    };
    const pending = this.queue.then(task);
    this.queue = pending.catch(() => {});
    return pending;
  }
  async openBoundContainer() {
    const binding = this.definition.container,
      position = new Vec3(binding.x, binding.y, binding.z);
    if (
      dimension(this.bot) !== binding.dimension ||
      this.bot.entity.position.distanceTo(position.offset(0.5, 0.5, 0.5)) > 5
    )
      throw new Error("Привязанный контейнер вне досягаемости");
    const block = this.bot.blockAt(position);
    if (!block || !["barrel", "chest"].includes(block.name))
      throw new Error("Привязанный контейнер не найден");
    return this.bot.openContainer(block);
  }
  async collectionQuantity(nickname, playerUuid) {
    if (this.busy) throw new Error("Банкомат занят");
    this.busy = true;
    const liveBot = this.bot;
    let container;
    try {
      this.coordinator.guard();
      if (!this.nearby(nickname, playerUuid))
        throw new Error("Игрок отошёл или сменил измерение");
      container = await this.openBoundContainer();
      if (!this.ready || this.bot !== liveBot)
        throw new Error("Соединение изменилось при открытии контейнера");
      this.requireUnnamedCurrency(container);
      const inventory = this.count(container.items()),
        space = this.capacity(
          container.slots.slice(0, container.inventoryStart),
        );
      if (!inventory)
        throw new Error("В банкомате нет дебрисов для инкассации");
      if (!space)
        throw new Error(
          "В контейнере нет свободного места для дебрисов. Освободите его и повторите инкассацию",
        );
      return Math.min(inventory, space);
    } finally {
      try {
        if (container) container.close();
      } finally {
        this.busy = false;
      }
    }
  }
  async execute(operation) {
    if (this.busy) throw new Error("Банкомат занят");
    this.busy = true;
    let container,
      proof = {};
    const id = operation.id,
      q = operation.quantity,
      liveBot = this.bot;
    try {
      this.coordinator.guard();
      if (!this.nearby(operation.nickname, operation.player_uuid))
        throw new Error("Игрок отошёл или сменил измерение");
      container = await this.openBoundContainer();
      if (!this.ready || this.bot !== liveBot)
        throw new Error("Соединение изменилось при открытии контейнера");
      this.requireUnnamedCurrency(container);
      proof = {
        inventory_before: this.count(container.items()),
        container_before: this.count(container.containerItems()),
      };
      const deposit = operation.kind === "deposit";
      if ((deposit ? proof.container_before : proof.inventory_before) < q)
        throw new Error(
          deposit
            ? "В контейнере недостаточно дебрисов"
            : "В банкомате недостаточно дебрисов",
        );
      const slots = deposit
        ? container.slots.slice(
            container.inventoryStart,
            container.inventoryEnd,
          )
        : container.slots.slice(0, container.inventoryStart);
      if (this.capacity(slots) < q)
        throw new Error("Недостаточно свободного места для дебрисов");
      await this.coordinator.journal.write(id, {
        ...operation,
        status: "ARMED",
        evidence: proof,
      });
      await this.coordinator.operation(id, "start", { evidence: proof });
      // Fsync the physical-intent record before the first inventory click.
      await this.coordinator.journal.write(id, {
        ...operation,
        status: "PROCESSING",
        evidence: proof,
      });
      this.coordinator.guard();
      if (!this.ready || this.bot !== liveBot)
        throw new Error("Соединение изменилось перед физическим действием");
      if (!this.nearby(operation.nickname, operation.player_uuid))
        throw new Error("Игрок отошёл перед выполнением операции");
      const item = this.bot.registry.itemsByName[this.config.currency_item];
      if (!item) throw new Error("Сервер не поддерживает ancient_debris");
      this.requireUnnamedCurrency(container);
      if (operation.kind === "collection") {
        for (let remaining = q; remaining > 0;) {
          this.coordinator.guard();
          if (
            !this.ready ||
            this.bot !== liveBot ||
            !this.nearby(operation.nickname, operation.player_uuid)
          )
            throw new Error(
              "Инкассация прервана; проверьте фактическое перемещение",
            );
          const batch = Math.min(
            remaining,
            this.config.max_operation_amount || 64,
          );
          this.requireUnnamedCurrency(container);
          await container.deposit(item.id, null, batch);
          remaining -= batch;
        }
      } else
        await (deposit
          ? container.withdraw(item.id, null, q)
          : container.deposit(item.id, null, q));
      let confirmed = false;
      for (let i = 0; i < 10; i++) {
        await sleep(200);
        if (!this.ready || this.bot !== liveBot)
          throw new Error("Соединение прервано во время перемещения");
        proof.inventory_after = this.count(container.items());
        proof.container_after = this.count(container.containerItems());
        if (exactMovement(operation.kind, q, proof)) {
          confirmed = true;
          break;
        }
      }
      if (!confirmed)
        throw new Error(
          "Не подтверждено точное изменение инвентаря и контейнера",
        );
      await this.coordinator.journal.write(id, {
        ...operation,
        status: "PHYSICAL_CONFIRMED",
        evidence: proof,
      });
      const result = await this.coordinator.operation(id, "confirm", {
        evidence: proof,
      });
      await this.coordinator.journal.write(id, { ...result, evidence: proof });
      return result;
    } catch (error) {
      this.error = String(error.message).slice(0, 500);
      // No automatic reverse inventory clicks: a lost API response may already
      // have committed. Unknown physical outcomes retain the reserve and evidence.
      const result = await this.coordinator
        .operation(id, "fail", { error: this.error, evidence: proof })
        .catch(() => null);
      await this.coordinator.journal.write(id, {
        ...operation,
        status: result?.status || "REQUIRES_REVIEW",
        evidence: proof,
        error: this.error,
      });
      throw new Error(
        this.error +
          " Операция: " +
          id +
          (result?.status === "FAILED"
            ? ". Резерв отменён."
            : ". Требуется проверка администратора."),
      );
    } finally {
      try {
        if (container) container.close();
      } catch {
        /* Closing never retries physical movement. */
      }
      this.busy = false;
      await this.coordinator.heartbeat().catch(() => {});
    }
  }
  stop() {
    clearTimeout(this.reconnectTimer);
    clearTimeout(this.loginTimer);
    this.ready = false;
    this.bot?.quit();
  }
}
