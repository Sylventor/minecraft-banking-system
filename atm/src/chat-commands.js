import { setTimeout as sleep } from "node:timers/promises";
import { chatCard, commandText } from "./chat-format.js";
import { helpMessages, previewMessages } from "./messages.js";

export class ChatCommands {
  constructor(coordinator, config, wait = sleep) {
    this.coordinator = coordinator;
    this.config = config;
    this.delay = Number.isFinite(config.message_delay_ms)
      ? Math.max(1500, config.message_delay_ms)
      : 1500;
    this.wait = wait;
    this.running = null;
    this.finishedAt = -Infinity;
  }

  identity(sender) {
    return [...this.coordinator.clients.values()]
      .map((client) => client.identity(sender))
      .find(Boolean);
  }

  async capabilities(sender) {
    const identity = this.identity(sender);
    if (!identity) throw new Error("Не удалось подтвердить игровую личность");
    const permissions = await this.coordinator.post("capabilities", {
      nickname: sender,
      player_uuid: identity.uuid,
    });
    return { identity, permissions };
  }

  async handle(sender, command) {
    if (!["help", "test"].includes(command.kind)) return false;
    const coordinator = this.coordinator;
    const master = coordinator.master();
    const nickname = master.definition.username;
    if (command.kind === "help") {
      const capabilities = await this.capabilities(sender).catch(() => null);
      for (const message of helpMessages(nickname, capabilities?.permissions))
        await master.message(sender, message);
      return true;
    }
    const { identity, permissions } = await this.capabilities(sender);
    if (permissions.can_test !== true)
      throw new Error("Нет права на тест сообщений банкомата");
    if (command.stop) {
      if (this.running && this.running.uuid !== identity.uuid)
        throw new Error(
          "Демонстрацию запустил другой администратор. Дождитесь её завершения.",
        );
      if (this.running) this.running.cancelled = true;
      else
        await master.message(
          sender,
          chatCard("Тест · Нет активной демонстрации", []),
        );
      return true;
    }
    if (this.running)
      throw new Error(
        "Демонстрация уже запущена. Дождитесь завершения или остановите свой показ.",
      );
    if (Date.now() - this.finishedAt < 30000)
      throw new Error(
        "Повторный тест доступен через 30 секунд после завершения предыдущего.",
      );

    const run = { uuid: identity.uuid, cancelled: false };
    this.running = run;
    let sent = 0;
    try {
      const messages = previewMessages(
        nickname,
        this.config.max_operation_amount,
      );
      await master.message(
        sender,
        chatCard("Тест · Начало", [
          `${messages.length} примеров. Все числа и код вымышлены.`,
          "Операции не выполняются. Кнопки открывают справку.",
          "Остановить: " + commandText(nickname, "$test stop"),
        ]),
      );
      for (const message of messages) {
        if (run.cancelled || coordinator.stopping) break;
        coordinator.guard();
        if (this.identity(sender)?.uuid !== run.uuid)
          throw new Error(
            "Демонстрация остановлена: игровая личность больше не подтверждена",
          );
        await master.message(sender, message);
        sent++;
        await this.wait(this.delay);
      }
      if (!coordinator.stopping)
        await master.message(
          sender,
          chatCard(run.cancelled ? "Тест · Остановлен" : "Тест · Завершён", [
            `Показано примеров: ${sent}. Реальных операций не было.`,
          ]),
        );
    } finally {
      this.running = null;
      this.finishedAt = Date.now();
    }
    return true;
  }
}
