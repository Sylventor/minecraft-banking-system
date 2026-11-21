import {
  hpayCancelMessage,
  hpayHelpMessages,
  hpayMessage,
} from "./messages.js";

export class HPayChat {
  constructor(coordinator, protectionSeconds) {
    this.coordinator = coordinator;
    this.protectionSeconds = protectionSeconds;
    this.quotes = new Map();
    this.rate = new Map();
    this.inFlight = new Set();
    this.deliveries = new Map();
  }

  async command(sender, command, delivery) {
    const coordinator = this.coordinator;
    const master = coordinator.master();
    const now = Date.now();
    const senderKey = sender.toLowerCase();
    for (const [id, until] of this.deliveries)
      if (until <= now) this.deliveries.delete(id);
    if (delivery && this.deliveries.has(delivery.id)) return;
    if (delivery) this.deliveries.set(delivery.id, now + 120_000);
    for (const [id, until] of this.rate) if (until <= now) this.rate.delete(id);
    if (this.rate.has(senderKey)) return;
    this.rate.set(senderKey, now + 750);
    if (command.kind === "payhelp") {
      for (const message of hpayHelpMessages(master.definition.username))
        await master.message(sender, message);
      return;
    }
    coordinator.guard();
    const identity =
      master.identity(sender) ||
      [...coordinator.clients.values()]
        .map((client) => client.identity(sender))
        .find(Boolean);
    if (!identity) throw new Error("Не удалось подтвердить игровую личность");
    const key = identity.uuid;
    if (
      typeof delivery?.sender === "string" &&
      delivery.sender.toLowerCase() !== key.toLowerCase()
    )
      return;
    for (const [id, quote] of this.quotes)
      if (quote.expires <= now) this.quotes.delete(id);
    if (this.inFlight.has(key)) return;

    const previous = this.quotes.get(key);
    const confirm =
      command.kind === "pay" && previous?.product === command.product;
    if (confirm && previous.confirmAfter > now) return;
    this.inFlight.add(key);
    try {
      const identityData = { nickname: sender, player_uuid: key };
      if (command.kind === "cancel") {
        this.quotes.delete(key);
        const response = await coordinator.post("hpay/cancel", identityData);
        await master.message(
          sender,
          hpayCancelMessage(response.status === "cancelled"),
        );
        return;
      }

      const payload = { ...identityData, product_id: command.product };
      if (confirm) {
        payload.purchase_id = previous.id;
        payload.protection_seconds = this.protectionSeconds;
      } else {
        this.quotes.delete(key);
      }
      const result = await coordinator.post(
        confirm ? "hpay/confirm" : "hpay/quote",
        payload,
      );
      if (result.status === "pending") {
        await master.message(
          sender,
          hpayMessage(master.definition.username, result, command.product),
          "hpay",
        );
        const expires = Date.parse(result.expires_at);
        this.quotes.set(key, {
          id: result.id,
          product: command.product,
          expires,
          confirmAfter: Date.now() + 750,
        });
        return;
      }
      this.quotes.delete(key);
      await master.message(
        sender,
        hpayMessage(master.definition.username, result, command.product),
      );
    } finally {
      this.inFlight.delete(key);
    }
  }
}
