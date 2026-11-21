"""Human-facing errors are Russian by default; machine-readable error codes stay stable."""

from __future__ import annotations

import json
from pathlib import Path

UK_MESSAGES: dict[str, str] = json.loads((Path(__file__).parent / "locales" / "uk.json").read_text(encoding="utf-8"))

MESSAGES = {
    "self trading is prohibited": "Самоторговля запрещена: встречная заявка принадлежит вам или тому же владельцу",
    "secondary trading is not active; IPO must have ended": "Вторичные торги недоступны: дождитесь окончания IPO и проверьте статус выпуска",
    "IPO not available": "Первичное размещение сейчас недоступно",
    "IPO quantity or buyer cap exceeded": "Недостаточно акций на IPO или превышен лимит покупателя",
    "insufficient available shares": "Недостаточно свободных акций: часть может быть зарезервирована",
    "insufficient available funds for order reserve": "Недостаточно свободных ДБ для резерва заявки",
    "another authorized approver required": "Ведомость должен утвердить другой уполномоченный представитель организации",
    "cannot moderate own issue": "Нельзя утверждать собственный выпуск: нужен другой биржевой администратор",
    "record date must have passed before snapshot approval": "Утверждение возможно после даты фиксации; ведомость должна оставаться черновиком",
    "approved unpaid dividend required": "Нужна утверждённая, ещё не выплаченная дивидендная ведомость",
    "dividend payment date has not arrived": "Дата выплаты дивидендов ещё не наступила",
    "insufficient funds for entire dividend": "Недостаточно свободных средств на всю дивидендную ведомость",
    "invalid listing transition": "Недопустимый переход статуса выпуска",
    "issuer cannot buy its own IPO": "Эмитент не может покупать собственное IPO",
    "idempotency payload mismatch": "Этот ключ уже связан с другим запросом. Сначала проверьте прежний результат",
    "authentication required": "Войдите в банк через Discord",
    "invalid bot authentication": "Не удалось подтвердить запрос Discord-бота",
    "Discord account is not linked": "Сначала войдите на сайт через Discord",
    "profile is not active": "Профиль заблокирован или недоступен",
    "session expired or revoked": "Сессия завершена. Войдите в банк заново",
    "session is not active": "Сессия недействительна. Войдите заново",
    "invalid API key": "Некорректный API-ключ",
    "API key is invalid or revoked": "API-ключ недействителен или отозван",
    "API key scope is not granted": "У API-ключа нет необходимого разрешения",
    "API key does not grant access to this account": "API-ключ не даёт доступа к этому счёту",
    "Discord OAuth is not configured": "Вход через Discord пока не настроен",
    "OAuth state mismatch": "Не удалось подтвердить запрос входа. Начните вход заново",
    "OAuth state is expired": "Срок запроса входа истёк. Начните вход заново",
    "Minecraft verification integration is not configured": "Подтверждение Minecraft пока недоступно: доверенный сервер не настроен",
    "wait one minute before issuing another link code": "Подождите минуту перед получением нового кода",
    "invalid link code": "Неверный код привязки",
    "link challenge expired": "Код привязки истёк",
    "link challenge was already used or revoked": "Код привязки уже использован или отменён",
    "Minecraft identity is already linked": "Этот Minecraft-аккаунт уже привязан к другому профилю",
    "unlink the existing Minecraft identity before linking another": "Сначала отвяжите текущий Minecraft-аккаунт",
    "Privacy request not found": "Запрос о данных не найден",
    "Profile not found": "Профиль не найден",
    "Ticket does not belong to the data subject": "Обращение не принадлежит заявителю",
    "Confirm the action and provide its reason": "Подтвердите действие и укажите причину",
    "Only a received request can be verified": "Проверить можно только полученный запрос",
    "Verify ownership before approving a request": "Проверьте принадлежность аккаунта до одобрения запроса",
    "Erasure permission required": "Требуется разрешение на удаление данных",
    "Review copies and disputes and record a basis for retained structural records": "Проверьте копии и споры, укажите основание сохранения необходимых записей",
    "Automatic processing or verified manual fulfilment is required": "Дождитесь автоматической обработки или подтвердите фактическое исполнение",
    "No final result to communicate": "Итоговый результат ещё не получен",
    "Verify the data subject before export": "Проверьте заявителя перед экспортом",
    "A future timezone-aware expiry and lawful basis are required": "Укажите будущий срок с часовым поясом и законное основание",
    "Active hold not found": "Действующая приостановка не найдена",
    "Invalid privacy export table": "Недопустимая таблица экспорта",
    "Invalid privacy export page": "Недопустимая страница экспорта",
    "profile processing is restricted": "Обработка профиля ограничена",
}
CODE_MESSAGES = {
    "validation_error": "Проверьте заполненные поля",
    "internal_error": "Не удалось обработать запрос. Попробуйте позже",
    "constraint_conflict": "Такие данные уже используются либо запрос противоречит текущему состоянию",
    "oauth_provider_unavailable": "Discord временно недоступен. Попробуйте войти позже",
    "core_permission_denied": "У вас нет прав на это действие",
    "core_not_found": "Счёт или операция не найдены",
    "core_invalid_argument": "Проверьте реквизиты и сумму операции",
    "core_failed_precondition": "Недостаточно доступных средств либо счёт заблокирован",
    "core_already_exists": "Запрос с этим ключом уже был выполнен с другими данными",
    "core_resource_exhausted": "Превышен лимит операции",
    "core_unavailable": "Банк временно недоступен. Для повтора используйте прежнее подтверждение",
    "core_deadline_exceeded": "Ответ банка задерживается. Повторите прежнее подтверждение без изменения данных",
    "core_internal": "Результат уточняется. Повторите то же подтверждение: повторного списания не будет",
}


def error_message(code: str, message: str, language: str = "ru") -> str:
    if language.lower().startswith("uk"):
        source = MESSAGES.get(message, message)
        return (
            UK_MESSAGES.get(source)
            or UK_MESSAGES.get(CODE_MESSAGES.get(code, ""))
            or "Дію відхилено. Перевірте дані та права доступу"
        )
    if language.startswith("en"):
        return message
    if message in MESSAGES:
        return MESSAGES[message]
    if any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in message):
        return message
    if code in CODE_MESSAGES:
        return CODE_MESSAGES[code]
    return "Действие недоступно. Проверьте права, реквизиты и состояние профиля"
