import { useEffect, useState } from "react";
import { api } from "../api";
import { useBank } from "../bank-context";
import { Empty, Icon, Loading, PageHeading } from "../components/BankUI";
import { Notice } from "../components/Notice";
import {
  dateTime,
  localError,
  useI18n,
  localizeSystem,
  notificationBody,
} from "../i18n";
import type { Notification } from "../types";
export function Notifications() {
  const { openReceipt, refresh, navigate } = useBank();
  const { t, language } = useI18n();
  const [items, setItems] = useState<Notification[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [deliveryAvailable, setDeliveryAvailable] = useState<boolean>();
  useEffect(() => {
    let active = true;
    api
      .notifications()
      .then((page) => {
        if (active) {
          setItems(page.items);
          setCursor(page.next_cursor);
          setUnread(page.unread);
          setDeliveryAvailable(page.discord_delivery_available);
        }
      })
      .catch((e) => {
        if (active) setError(localError(e, language));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [language]);
  async function readAll() {
    setBusy(true);
    try {
      await api.readNotifications([], true);
      const page = await api.notifications();
      setItems(page.items);
      setCursor(page.next_cursor);
      setUnread(page.unread);
      await refresh();
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function open(item: Notification) {
    try {
      if (!item.read_at) {
        await api.readNotifications([item.id]);
        setItems((value) =>
          value.map((note) =>
            note.id === item.id
              ? { ...note, read_at: new Date().toISOString() }
              : note,
          ),
        );
        setUnread((value) => Math.max(0, value - 1));
      }
      if (item.operation_id) openReceipt(item.operation_id);
    } catch (e) {
      setError(localError(e, language));
    }
  }
  async function more() {
    if (!cursor) return;
    setBusy(true);
    try {
      const page = await api.notifications(cursor);
      setItems((value) => [...value, ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeading
        title={t("Уведомления", "Notifications")}
        subtitle={t(
          "Переводы, безопасность и важные события банка",
          "Transfers, security and important bank events",
        )}
        action={
          unread > 0 && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void readAll()}
            >
              <Icon name="check" />
              {t("Прочитать все", "Mark all as read")}
            </button>
          )
        }
      />
      <Notice error={error} />
      <section className="panel">
        {busy && !items.length ? (
          <Loading />
        ) : items.length ? (
          <div className="notification-list">
            {items.map((note) => (
              <button
                key={note.id}
                className={!note.read_at ? "unread" : ""}
                onClick={() => void open(note)}
              >
                <span className={"notification-icon " + note.kind}>
                  <Icon
                    name={
                      note.kind === "security"
                        ? "shield"
                        : note.kind === "pending"
                          ? "clock"
                          : "bell"
                    }
                  />
                </span>
                <span>
                  <b>{localizeSystem(note.title)}</b>
                  <p>{notificationBody(note.body, note.kind, note.title)}</p>
                  <small>{dateTime(note.created_at, language)}</small>
                </span>
                {!note.read_at && <span className="unread-dot" />}
                {note.operation_id && <Icon name="chevron" size={16} />}
              </button>
            ))}
          </div>
        ) : (
          !error && (
            <Empty
              icon="bell"
              title={t("Здесь спокойно", "All quiet here")}
              description={t(
                "Мы сообщим о переводах и важных изменениях безопасности.",
                "Transfer and security updates will appear here.",
              )}
            />
          )
        )}{" "}
        {cursor && (
          <button
            className="secondary load-more"
            disabled={busy}
            onClick={() => void more()}
          >
            {t("Загрузить ещё", "Load more")}
          </button>
        )}
      </section>
      <div className="info-box">
        <Icon name="info" />
        {t(
          deliveryAvailable
            ? "Уведомления доступны в кабинете и через подключённый сервис Discord. Проверьте настройки доставки и доступность личных сообщений."
            : "Уведомления доступны в кабинете. Сервис доставки Discord сейчас не подключён.",
          deliveryAvailable
            ? "Notifications are available here and through the connected Discord service. Check your delivery and direct message preferences."
            : "Notifications are available here. Discord delivery is currently disconnected.",
        )}
        <button
          className="text-button"
          onClick={() => {
            navigate("settings");
            location.hash = "/settings?tab=notifications";
          }}
        >
          {t("Настроить", "Preferences")}
        </button>
      </div>
    </>
  );
}
