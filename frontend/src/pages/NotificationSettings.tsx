import { localize as ui, useI18n as useLocale } from "../i18n";
import { useEffect, useState } from "react";
import {
  notificationApi,
  type Delivery,
  type NotificationPreferences,
} from "../admin-api";
import { Loading } from "../components/BankUI";
import { Notice } from "../components/Notice";
import { dateTime, localError, localizeSystem } from "../i18n";
const labels = (): Record<string, string> => ({
  sent: ui("Доставлено"),
  pending: ui("В очереди"),
  sending: ui("Отправляется"),
  failed: ui("Не доставлено"),
  suppressed: ui("Отключено настройками"),
});
const errors = (): Record<string, string> => ({
  dm_closed: ui("Личные сообщения Discord закрыты"),
  rate_limited: ui("Ожидание лимита Discord"),
  recipient_unavailable: ui("Discord-профиль недоступен"),
  discord_unavailable: ui("Discord временно недоступен"),
  delivery_unknown: ui("Ответ не получен; число попыток ограничено"),
});
export function NotificationSettings() {
  useLocale();
  const [prefs, setPrefs] = useState<NotificationPreferences>();
  const [items, setItems] = useState<Delivery[]>([]);
  const [online, setOnline] = useState(false),
    [next, setNext] = useState<number | null>();
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false);
  async function load(offset = 0) {
    try {
      const data = await notificationApi.deliveries(offset);
      setItems(data.items);
      setNext(data.next_offset);
      setOnline(data.worker_online);
    } catch (e) {
      setError(localError(e));
    }
  }
  useEffect(() => {
    let active = true;
    notificationApi
      .preferences()
      .then((x) => {
        if (active) setPrefs(x);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    void load();
    return () => {
      active = false;
    };
  }, []);
  async function save() {
    if (!prefs) return;
    setBusy(true);
    setError("");
    try {
      setPrefs(await notificationApi.save(prefs));
      setSaved(true);
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  const fields: [keyof NotificationPreferences, string][] = [
    ["discord_enabled", ui("Получать личные сообщения Discord")],
    ["incoming_enabled", ui("Входящие переводы и зачисления")],
    ["outgoing_enabled", ui("Исходящие переводы и списания")],
    ["organizations_enabled", ui("Организации: роли и операции")],
    ["security_enabled", ui("События безопасности")],
    ["transfers_enabled", ui("Финансовые уведомления")],
  ];
  return (
    <section className="panel notification-settings">
      <div className="section-heading">
        <h2>{ui("Уведомления")}</h2>
        <span className={"tag " + (online ? "positive" : "")}>
          {online
            ? ui("Discord подключён")
            : ui("Сервис доставки ожидает подключения")}
        </span>
      </div>
      <Notice error={error} />
      {!prefs ? (
        <Loading />
      ) : (
        <>
          <fieldset>
            <legend>{ui("Личные сообщения Discord")}</legend>
            {fields.map(([key, label]) => (
              <label className="checkbox" key={key}>
                <input
                  type="checkbox"
                  checked={prefs[key]}
                  onChange={(e) => {
                    setPrefs({ ...prefs, [key]: e.target.checked });
                    setSaved(false);
                  }}
                />
                {label}
              </label>
            ))}
          </fieldset>
          <p className="field-hint">
            {ui(
              "Критические события — изменение прав, блокировка и безопасность входа — доставляются независимо от этих настроек. Откройте личные сообщения от бота Banking System в Discord.",
            )}
          </p>
          <button onClick={() => void save()} disabled={busy}>
            {busy ? ui("Сохраняем…") : ui("Сохранить уведомления")}
          </button>
          {saved && (
            <p className="success-banner" role="status">
              {ui("Настройки уведомлений сохранены")}
            </p>
          )}
        </>
      )}
      <div className="section-heading">
        <h3>{ui("Статус доставки")}</h3>
        <button className="text-button" onClick={() => void load()}>
          {ui("Обновить")}
        </button>
      </div>
      {!items.length && <p className="muted">{ui("Отправок пока нет.")}</p>}
      {items.map((item) => (
        <div className="delivery-row" key={item.id}>
          <span>
            <b>{localizeSystem(item.title)}</b>
            <small>
              {dateTime(item.created_at, undefined)}
              {ui(" · попыток: ")}
              {item.attempts}
              {item.last_error &&
                " · " + (errors()[item.last_error] || ui("Ошибка доставки"))}
            </small>
          </span>
          <span className="tag">{labels()[item.status] || item.status}</span>
        </div>
      ))}
      {next != null && (
        <button className="secondary" onClick={() => void load(next)}>
          {ui("Следующая страница")}
        </button>
      )}
    </section>
  );
}
