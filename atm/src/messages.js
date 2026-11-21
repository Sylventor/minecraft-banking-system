import {
  bankLink,
  chatCard,
  chatCommand,
  chatError,
  chatText,
  commandText,
  currencyIcon,
} from "./chat-format.js";

export function hpayHelpMessages(nickname) {
  return [
    chatCard("HPay · Оплата товаров", [
      commandText(nickname, "$pay 1234") + " — посмотреть цену",
      "Повтори ту же команду после ответа, чтобы оплатить.",
      "На табличке H-1234 используй число 1234.",
    ]),
    chatCard("HPay · Команды", [
      commandText(nickname, "$cancel") + " — отменить",
      commandText(nickname, "$payhelp") + " — справка",
    ]),
  ];
}

export function helpMessages(nickname, capabilities = {}) {
  const messages = [
    chatCard("Banking System · Банкомат", [
      commandText(nickname, "$bal") + " — баланс",
      commandText(nickname, "$with N") + " — снять",
      commandText(nickname, "$dep N") + " — внести",
    ]),
    chatCard("Banking System · Переводы", [
      commandText(nickname, "$transaction Ник Сумма"),
      "Перевод игроку. Сумма может быть дробной: 12.50.",
    ]),
    ...hpayHelpMessages(nickname),
    chatCard("Banking System · Сайт", [
      bankLink,
      "Привязка Minecraft — в настройках сайта.",
    ]),
  ];
  const admin = [];
  if (capabilities.can_collect)
    admin.push(commandText(nickname, "$clear-atm") + " — инкассация");
  if (capabilities.can_test)
    admin.push(
      commandText(nickname, "$test") + " — примеры сообщений",
      commandText(nickname, "$test stop") + " — остановить показ",
    );
  if (admin.length)
    messages.push(chatCard("Banking System · Администратору", admin));
  return messages;
}

export function balanceMessage(balance, available) {
  return chatCard(
    "Banking System · Баланс",
    [
      "Всего: " + balance + " " + currencyIcon,
      "Доступно: " + available + " " + currencyIcon,
    ],
    "aqua",
  );
}

export function transferMessage(recipient, amount) {
  return chatCard(
    "Перевод выполнен",
    ["Получатель: " + recipient, "Сумма: " + amount + " " + currencyIcon],
    "green",
  );
}

export function physicalMessage(kind, quantity) {
  return chatCard(
    kind === "collection"
      ? "Инкассация завершена"
      : kind === "deposit"
        ? "Пополнение выполнено"
        : currencyIcon + " выданы в контейнер",
    ["Количество: " + quantity + " " + currencyIcon],
    "green",
  );
}

export function alreadyCompletedMessage() {
  return chatCard("Banking System", ["Эта операция уже выполнена."]);
}

export function linkMessage(code) {
  return chatCard("Banking System · Привязка Minecraft", [
    "Код подтверждения: " + code,
  ]);
}

export function errorMessage(message, nickname, hpay = false) {
  const detail = String(message).replaceAll(
    "[$help]",
    commandText(nickname, "$help"),
  );
  return chatCard(
    hpay ? "HPay · Ошибка" : "Banking System · Ошибка",
    [chatError(detail, hpay ? 140 : 185)],
    "red",
  );
}

export function hpayCancelMessage(cancelled) {
  return chatCard(
    cancelled ? "HPay · Платёж отменён" : "HPay · Нет ожидающего платежа",
    [],
  );
}

export function hpayMessage(nickname, result, productId) {
  const item = chatText(result.product_name, 32);
  const merchant = chatText(result.merchant_name, 24);
  const amount = result.price.replace(".", ",");
  if (result.status === "pending") {
    const action = chatCommand(nickname, "$pay " + productId, "Оплатить");
    const suggested = "60 с: " + action + " → Enter";
    const details = [
      "Продавец: " + merchant,
      "К оплате: " + amount + " " + currencyIcon,
    ];
    const promptLength = (
      "<br>" +
      chatCard("HPay · Подтверждение", [
        "Товар: " + item,
        ...details,
        suggested,
      ])
    ).length;
    details.push(
      promptLength <= 240
        ? suggested
        : "За 60 с: " + commandText(nickname, "$pay " + productId),
    );
    const itemBudget =
      240 -
      ("<br>" + chatCard("HPay · Подтверждение", ["Товар: ", ...details]))
        .length;
    return chatCard("HPay · Подтверждение", [
      "Товар: " + chatText(item, Math.max(0, itemBudget)),
      ...details,
    ]);
  }
  if (result.status === "completed")
    return chatCard(
      "HPay · Оплачено",
      [
        "Товар: " + item,
        "Продавец: " + merchant,
        "Списано: " + amount + " " + currencyIcon,
      ],
      "green",
    );
  return chatCard(
    "HPay · Платёж закрыт",
    [chatCommand(nickname, "$pay " + productId, "Новая покупка")],
    "red",
  );
}

export function previewMessages(nickname, maximum = 64) {
  const sample = {
    product_name: "Алмазная кирка",
    merchant_name: "Тестовый магазин",
    price: "12.50",
  };
  const errors = [
    "Неизвестная команда или сумма. Используйте [$help].",
    "Не удалось подтвердить игровую личность. Убедитесь, что вы вошли на сервер.",
    "Привяжите Minecraft-аккаунт в настройках Banking System. Ник и UUID должны совпадать.",
    "Нет права на инкассацию банкомата",
    "Нет права на тест сообщений банкомата",
    "Основной личный счёт недоступен. Откройте личный кабинет банка.",
    "Недостаточно средств",
    "Получатель не найден или не привязал Minecraft-аккаунт",
    "Банковский API недоступен",
    "Нет действующей связи с координатором банка",
    "Мастер-бот недоступен",
    "Сервер не настроен для надёжного подтверждения доставки",
    "Игрок не найден сервером",
    "Сервер не подтвердил доставку",
    "Банкомат рядом занят или ожидает проверки предыдущей операции. Проверьте его статус в админке.",
    "Рядом нет подключённого банкомата, который видит вас. Проверьте подключение ATM и подойдите ближе.",
    "Количество должно быть от 1 до " + maximum + " ДБ.",
    "Выбранный банкомат отключён",
    "Банкомат занят",
    "Игрок отошёл или сменил измерение",
    "Привязанный контейнер вне досягаемости",
    "Привязанный контейнер не найден",
    "Соединение изменилось при открытии контейнера",
    "В банкомате нет дебрисов для инкассации",
    "В контейнере нет свободного места для дебрисов. Освободите его и повторите инкассацию",
    "В контейнере недостаточно дебрисов",
    "В банкомате недостаточно дебрисов",
    "Недостаточно свободного места для дебрисов",
    "Переименованные древние обломки не принимаются. Уберите их из контейнера и инвентаря банкомата",
    "Соединение изменилось перед физическим действием",
    "Игрок отошёл перед выполнением операции",
    "Сервер не поддерживает ancient_debris",
    "Инкассация прервана; проверьте фактическое перемещение",
    "Соединение прервано во время перемещения",
    "Не подтверждено точное изменение инвентаря и контейнера",
    "Контейнер недоступен. Операция: 00000000-0000-4000-8000-000000000001. Резерв отменён.",
    "Частичное перемещение. Операция: 00000000-0000-4000-8000-000000000002. Требуется проверка администратора.",
  ];
  const messages = [
    ...helpMessages(nickname, { can_collect: true, can_test: true }),
    balanceMessage("1250.75", "1200.50"),
    transferMessage("DemoPlayer", "25.50"),
    physicalMessage("withdraw", 16),
    physicalMessage("deposit", 32),
    physicalMessage("collection", 1728),
    alreadyCompletedMessage(),
    linkMessage("123456"),
    hpayCancelMessage(true),
    hpayCancelMessage(false),
    hpayMessage(nickname, { ...sample, status: "pending" }, "1234"),
    hpayMessage(
      nickname,
      {
        product_name: "Длинное тестовое название товара ".repeat(3),
        merchant_name: "Длинное название магазина ".repeat(3),
        price: "9999999999999999.99",
        status: "pending",
      },
      "9223372036854775807",
    ),
    hpayMessage(nickname, { ...sample, status: "completed" }, "1234"),
    hpayMessage(nickname, { ...sample, status: "expired" }, "1234"),
    ...errors.map((message) => errorMessage(message, nickname)),
    ...["Товар не найден", "Предложение истекло", "Недостаточно средств"].map(
      (message) => errorMessage(message, nickname, true),
    ),
  ];
  // Demo buttons may only open help; sample purchases must never be submitted.
  return messages.map((message) =>
    message.replace(
      /<click:suggest_command:'[^'<>\r\n]*'>/g,
      `<click:suggest_command:'/msg ${nickname} $help'>`,
    ),
  );
}
