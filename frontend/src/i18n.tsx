import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { setRequestLanguage, ApiFailure } from "./api";

import catalogData from "./locales/catalog.json";
const catalog: Record<string, { en: string; uk: string }> = catalogData;
export type Language = "ru" | "en" | "uk-UA";
export function normalizeLanguage(value: string | null | undefined): Language {
  return value === "en"
    ? "en"
    : value === "uk" || value === "uk-UA"
      ? "uk-UA"
      : "ru";
}
let activeLanguage: Language = normalizeLanguage(
  typeof localStorage === "undefined"
    ? null
    : localStorage.getItem("horizon.language"),
);
export function currentLanguage(): Language {
  return activeLanguage;
}
export function languageLocale(language: Language = activeLanguage): string {
  return language === "ru" ? "ru-RU" : language === "en" ? "en-GB" : "uk-UA";
}
export function translate(
  language: Language,
  ru: string,
  en?: string,
  uk?: string,
): string {
  const key = ru.replace(/\s+/g, " ").trim();
  const item = catalog[key];
  if (language === "ru") return ru;
  if (language === "en" && en !== undefined) return en;
  if (language === "uk-UA" && uk !== undefined) return uk;
  const translated = language === "en" ? (en ?? item?.en) : (uk ?? item?.uk);
  if (translated === undefined) return ru;
  return (
    (ru.startsWith(" ") ? " " : "") + translated + (ru.endsWith(" ") ? " " : "")
  );
}
/** Translate application-owned labels; never pass names or user-authored content. */
export function localize(ru: string): string {
  return translate(activeLanguage, ru);
}
export function localizeTemplate(key: string, ...values: unknown[]): string {
  return localize(key).replace(/\{(\d+)\}/g, (match, index: string) =>
    Number(index) < values.length ? String(values[Number(index)]) : match,
  );
}

const reverseLabels = new Map(
  Object.entries(catalog).flatMap(
    ([ru, value]) =>
      [
        [value.en, ru],
        [value.uk, ru],
      ] as [string, string][],
  ),
);
const systemTemplates = Object.entries(catalog)
  .filter(([key]) => /\{\d+\}/.test(key))
  .sort(
    ([a], [b]) =>
      b.replace(/\{\d+\}/g, "").length - a.replace(/\{\d+\}/g, "").length,
  )
  .flatMap(([ru, value]) =>
    [ru, value.en, value.uk].map((source) => {
      const slots: number[] = [];
      const pattern = source
        .split(/(\{\d+\})/)
        .map((part) => {
          const slot = /^\{(\d+)\}$/.exec(part);
          if (slot) {
            slots.push(Number(slot[1]));
            return "([\\s\\S]*?)";
          }
          return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        })
        .join("");
      return { ru, slots, pattern: new RegExp("^" + pattern + "$") };
    }),
  );
/** Only for application-generated errors, events and notification titles. */
export function localizeSystem(text: string): string {
  if (catalog[text]) return localize(text);
  const source = reverseLabels.get(text);
  if (source) return localize(source);
  if (text.startsWith("Статус: ") && text.includes(" → ")) {
    const [before, after] = text.slice(8).split(" → ");
    return (
      localize("Статус:") + " " + localize(before) + " → " + localize(after)
    );
  }
  for (const item of systemTemplates) {
    const match = item.pattern.exec(text);
    if (!match) continue;
    const values: string[] = [];
    item.slots.forEach((slot, index) => {
      values[slot] = match[index + 1];
    });
    return localizeTemplate(item.ru, ...values);
  }
  return text;
}

export function notificationBody(
  body: string,
  kind: string,
  title: string,
): string {
  if (kind === "security") return localizeSystem(body);
  if (kind === "support" && !title.startsWith("Вам назначено"))
    return localizeSystem(body);
  if (kind === "transfer") {
    const amount = /^(-?\d+\.\d{2}) ДБ · (.*)$/s.exec(body);
    if (amount) return money(amount[1]) + " · " + amount[2];
    if (["Статус перевода уточняется", "Перевод не выполнен"].includes(title))
      return localizeSystem(body);
  }
  if (
    kind === "organization" &&
    body === "Проверьте изменения в личном кабинете."
  )
    return localizeSystem(body);
  return body;
}
type Translation = (ru: string, en: string, uk?: string) => string;
const context = createContext<{
  language: Language;
  setLanguage: (language: Language) => void;
  t: Translation;
}>({ language: "ru", setLanguage: () => undefined, t: (ru) => ru });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(() =>
    normalizeLanguage(localStorage.getItem("horizon.language")),
  );
  activeLanguage = language;
  useEffect(() => {
    localStorage.setItem("horizon.language", language);
    document.documentElement.lang = language;
    setRequestLanguage(language);
  }, [language]);
  return (
    <context.Provider
      value={{
        language,
        setLanguage,
        t: (ru, en, uk) => translate(language, ru, en, uk),
      }}
    >
      {children}
    </context.Provider>
  );
}
export function useI18n() {
  return useContext(context);
}

export function currencyLabel(language: Language): "ДБ" | "DB" | "dB" {
  return language === "ru" ? "ДБ" : language === "uk-UA" ? "dB" : "DB";
}

export function localError(
  error: unknown,
  language: Language = activeLanguage,
): string {
  const messages: Record<string, [string, string]> = {
    core_permission_denied: [
      "У вас нет разрешения на выполнение этой операции",
      "You do not have permission for this action",
    ],
    core_failed_precondition: [
      "На выбранном счёте недостаточно доступных средств либо счёт заблокирован",
      "Insufficient available funds or account blocked",
    ],
    core_not_found: [
      "Счёт или операция не найдены",
      "Account or operation not found",
    ],
    core_resource_exhausted: [
      "Превышен лимит операции",
      "Operation limit exceeded",
    ],
    core_unavailable: [
      "Банк временно недоступен. Повторите прежнее подтверждение",
      "Bank temporarily unavailable. Retry the same confirmation",
    ],
    core_deadline_exceeded: [
      "Ответ задерживается. Повторите прежнее подтверждение без изменения данных",
      "Response delayed. Retry the same confirmation without changing details",
    ],
    validation_error: ["Проверьте заполненные поля", "Check the form fields"],
    csrf_rejected: [
      "Не удалось подтвердить запрос. Обновите страницу",
      "Could not verify request. Refresh the page",
    ],
    rate_limited: [
      "Слишком много запросов. Подождите минуту",
      "Too many requests. Please wait a minute",
    ],
    constraint_conflict: [
      "Данные уже используются или недоступны",
      "Details are already in use or unavailable",
    ],
    internal_error: [
      "Не удалось обработать запрос. Попробуйте позже",
      "Could not process request. Try again later",
    ],
  };
  if (language === "uk-UA") {
    if (error instanceof ApiFailure) {
      if (error.code === "INSUFFICIENT_FUNDS")
        return "На вибраному рахунку недостатньо коштів для переказу";
      if (error.code === "PERMISSION_DENIED")
        return "У вас немає дозволу на виконання цієї операції";
      if (error.status === 401) return "Сесію завершено. Увійдіть знову";
      const known = messages[error.code];
      if (known) return translate(language, known[0], known[1]);
      const source = reverseLabels.get(error.message) ?? error.message;
      const rendered = translate(language, source);
      if (catalog[source] || /[іїєґ]/i.test(error.message)) return rendered;
      return "Дію відхилено. Перевірте дані та права доступу";
    }
    return "Не вдалося зв’язатися з банком. Якщо ви підтверджували переказ, повторіть те саме підтвердження";
  }
  if (error instanceof ApiFailure) {
    if (error.code === "INSUFFICIENT_FUNDS")
      return language === "ru"
        ? "На выбранном счёте недостаточно средств для выполнения перевода"
        : "The selected account has insufficient funds for this transfer";
    if (error.code === "PERMISSION_DENIED")
      return language === "ru"
        ? "У вас нет разрешения на выполнение этой операции"
        : "You do not have permission to perform this operation";
    if (error.status === 401)
      return language === "ru"
        ? "Сессия завершена. Войдите через Discord ещё раз"
        : "Your session has ended. Sign in with Discord again";
    const translation = messages[error.code];
    if (translation) return translation[language === "ru" ? 0 : 1];
    if (language === "ru" && /[а-яё]/i.test(error.message))
      return error.message;
    if (language === "en" && !/[а-яё]/i.test(error.message))
      return error.message;
    return language === "ru"
      ? "Действие отклонено. Проверьте данные и права доступа"
      : "Request rejected. Check details and access permissions";
  }
  return language === "ru"
    ? "Не удалось связаться с банком. Если вы подтверждали перевод, повторите то же подтверждение"
    : "Could not reach the bank. If confirming a transfer, retry the same confirmation";
}

export function normalizeAmount(input: string): string | null {
  const value = input
    .trim()
    .replace(/\u00a0|\u202f| /g, "")
    .replace(",", ".");
  if (!/^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  const result = `${whole}.${fraction.padEnd(2, "0")}`;
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0")) > 0n
    ? result
    : null;
}
export function money(
  amount: string,
  language: Language = activeLanguage,
  currency = true,
): string {
  if (!/^-?\d+\.\d{2}$/.test(amount)) return "—";
  const negative = amount.startsWith("-");
  const [whole, fraction] = amount.replace("-", "").split(".");
  return `${negative ? "−" : ""}${new Intl.NumberFormat(languageLocale(language)).format(BigInt(whole))}${language !== "en" ? "," : "."}${fraction}${currency ? ` ${currencyLabel(language)}` : ""}`;
}
export function dateTime(
  value: string,
  language: Language = activeLanguage,
  withTime = true,
): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat(languageLocale(language), {
    day: "numeric",
    month: "short",
    ...(withTime
      ? { hour: "2-digit", minute: "2-digit" }
      : { year: "numeric" }),
  }).format(parsed);
}
export function operationName(type: string, t: Translation): string {
  const names: Record<string, [string, string]> = {
    transfer: ["Перевод", "Transfer"],
    payment: ["Оплата", "Payment"],
    refund: ["Возврат", "Refund"],
    issue: ["Поступление от банка", "Bank allocation"],
    hold: ["Блокировка средств", "Funds reserved"],
    capture_hold: ["Списание резерва", "Reserved funds captured"],
    release_hold: ["Снятие блокировки", "Funds released"],
  };
  const pair = names[type] ?? ["Операция", "Operation"];
  return t(...pair);
}

export function operationStatus(status: string, t: Translation): string {
  const names: Record<string, [string, string]> = {
    completed: ["Выполнено", "Completed"],
    pending: ["Ожидает", "Pending"],
    processing: ["Выполняется", "Processing"],
    failed: ["Ошибка", "Failed"],
    cancelled: ["Отменено", "Cancelled"],
  };
  return names[status] ? t(...names[status]) : status;
}
