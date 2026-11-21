import { projectName } from "../branding";
import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../api";
import { useBank } from "../bank-context";
import { dateTime, localError } from "../i18n";
import {
  supportCategories,
  supportStatuses,
  supportPath,
  supportWrite,
  type SupportMessage,
  type SupportTicket,
} from "../support-api";
import { localizeSystem } from "../i18n";
import { Icon, Loading } from "./BankUI";
import { Notice } from "./Notice";
export function useSupportLive(
  admin: boolean,
  onChange: (unread?: number) => void,
  enabled = true,
) {
  const callback = useRef(onChange);
  useEffect(() => {
    callback.current = onChange;
  }, [onChange]);
  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | undefined,
      retry: ReturnType<typeof setTimeout> | undefined,
      stopped = false,
      delay = 1000;
    const connect = () => {
      if (stopped || typeof WebSocket === "undefined") return;
      const url = new URL(
        (import.meta.env.VITE_API_BASE ?? "/api/v1") + "/support/live",
        location.origin,
      );
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      if (admin) url.searchParams.set("admin", "1");
      socket = new WebSocket(url);
      socket.onopen = () => {
        delay = 1000;
      };
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === "support.changed") callback.current(data.unread);
        } catch {
          /* Ignore malformed transport data. */
        }
      };
      socket.onclose = (event) => {
        if (!stopped && event.code !== 1008) {
          retry = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 30000);
        }
      };
    };
    connect();
    const fallback = setInterval(() => {
      if (!socket || socket.readyState !== 1) callback.current();
    }, 15000);
    return () => {
      stopped = true;
      socket?.close();
      clearTimeout(retry);
      clearInterval(fallback);
    };
  }, [admin, enabled]);
}
export function SupportShortcut() {
  useLocale();
  const [open, setOpen] = useState(false),
    [unread, setUnread] = useState(0);
  const { refresh } = useBank();
  const close = useCallback(() => setOpen(false), []);
  const update = useCallback((value?: number) => {
    if (value !== undefined) setUnread(value);
    else
      void request<{
        unread: number;
      }>("/support/unread")
        .then((v) => setUnread(v.unread))
        .catch(() => {});
    window.dispatchEvent(new Event("support-updated"));
  }, []);
  useSupportLive(false, (value) => {
    update(value);
    void refresh().catch(() => {});
  });
  useEffect(() => {
    update();
  }, [update]);
  return (
    <>
      <button
        className="icon-button support-shortcut"
        title={ui("Поддержка")}
        aria-label={uit(
          "Поддержка{0}",
          unread ? uit(", непрочитанных: {0}", unread) : "",
        )}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="headset" />
        {unread > 0 && (
          <span className="support-badge">{unread > 99 ? "99+" : unread}</span>
        )}
      </button>
      {open && <SupportDrawer onClose={close} />}
    </>
  );
}
function SupportDrawer({ onClose }: { onClose: () => void }) {
  useLocale();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const items = ref.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input,textarea,select,[tabindex="0"]',
        );
        if (!items?.length) return;
        const first = items[0],
          last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", key);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="support-backdrop" onClick={onClose}>
      <div
        className="support-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={ui("Чат поддержки")}
        tabIndex={-1}
        ref={ref}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <small>{projectName}</small>
            <h2>{ui("Поддержка")}</h2>
          </div>
          <button
            className="icon-button"
            title={ui("Закрыть поддержку")}
            aria-label={ui("Закрыть поддержку")}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>
        <SupportPanel />
      </div>
    </div>
  );
}
export function SupportPanel({
  admin = false,
  permissions = [],
}: {
  admin?: boolean;
  permissions?: string[];
}) {
  useLocale();
  const [tickets, setTickets] = useState<SupportTicket[]>(),
    [selected, setSelected] = useState<string>();
  const [detail, setDetail] = useState<SupportTicket>(),
    [creating, setCreating] = useState(false);
  const [title, setTitle] = useState(""),
    [category, setCategory] = useState("other"),
    [body, setBody] = useState("");
  const [status, setStatus] = useState(""),
    [filterCategory, setFilterCategory] = useState(""),
    [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0),
    [next, setNext] = useState<number | null>(null);
  const [operators, setOperators] = useState<
    {
      id: string;
      display_name: string;
    }[]
  >([]);
  const [note, setNote] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const scroll = useRef<HTMLDivElement>(null),
    pinned = useRef(true),
    selectedRef = useRef(selected);
  const pending = useRef<
      | {
          signature: string;
          key: string;
        }
      | undefined
    >(undefined),
    sequence = useRef(0);
  const root = supportPath(admin),
    canReply = !admin || permissions.includes("admin.support.reply"),
    canManage = admin && permissions.includes("admin.support.manage");
  useEffect(() => {
    selectedRef.current = selected;
    setBody("");
    setNote(false);
    pending.current = undefined;
    pinned.current = true;
  }, [selected]);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const result = await request<{
        items: SupportTicket[];
        next_offset: number | null;
      }>(
        root +
          "?" +
          new URLSearchParams({
            ...(status ? { status } : {}),
            ...(filterCategory ? { category: filterCategory } : {}),
            q: search,
            offset: String(offset),
          }),
      );
      if (current !== sequence.current) return;
      setTickets(result.items);
      setNext(result.next_offset);
      if (selectedRef.current) {
        const id = selectedRef.current;
        const value = await request<SupportTicket>(root + "/" + id);
        if (selectedRef.current === id && current === sequence.current)
          setDetail((old) => {
            const messages =
              old?.id === id
                ? [...(old.messages || []), ...(value.messages || [])]
                : value.messages || [];
            return {
              ...value,
              notes: [
                ...new Map(
                  [
                    ...(old?.id === id ? old.notes || [] : []),
                    ...(value.notes || []),
                  ].map((n) => [n.id, n]),
                ).values(),
              ].sort((a, b) => a.id - b.id),
              notes_has_older:
                old?.id === id ? old.notes_has_older : value.notes_has_older,
              messages: [
                ...new Map(messages.map((m) => [m.id, m])).values(),
              ].sort((a, b) => a.id - b.id),
              has_older: old?.id === id ? old.has_older : value.has_older,
            };
          });
      }
      setError("");
    } catch (e) {
      if (current === sequence.current) setError(localError(e));
    }
  }, [root, status, filterCategory, search, offset]);
  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 200);
    return () => clearTimeout(timer);
  }, [load, selected]);
  useEffect(() => {
    const update = () => {
      void load();
    };
    window.addEventListener("support-updated", update);
    return () => window.removeEventListener("support-updated", update);
  }, [load]);
  useSupportLive(
    admin,
    () => {
      void load();
    },
    admin,
  );
  useEffect(() => {
    if (admin)
      void request<typeof operators>("/admin/support/staff")
        .then(setOperators)
        .catch((e) => setError(localError(e)));
  }, [admin]);
  const mark = useCallback(() => {
    const latest = detail?.messages?.at(-1)?.id;
    if (!latest || !pinned.current || document.visibilityState === "hidden")
      return;
    void supportWrite(root + "/" + detail!.id + "/read", {
      through_id: latest,
    }).catch(() => {});
  }, [detail, root]);
  useEffect(() => {
    if (pinned.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
    mark();
  }, [detail?.messages?.at(-1)?.id, mark]);
  async function send() {
    if (busy || !body.trim()) return;
    if (
      !creating &&
      detail &&
      ["closed", "resolved"].includes(detail.status) &&
      !note
    )
      return;
    const signature = JSON.stringify([
      creating ? "new" : selected,
      title,
      category,
      body.trim(),
      note,
    ]);
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const value = await supportWrite<SupportTicket>(
        creating
          ? root
          : root + "/" + selected + (note ? "/notes" : "/messages"),
        {
          body: body.trim(),
          client_id: pending.current.key,
          ...(creating ? { title, category } : {}),
        },
      );
      pending.current = undefined;
      setBody("");
      setCreating(false);
      setSelected(value.id);
      setDetail(value);
      pinned.current = true;
      await load();
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  async function change(value: object) {
    if (!selected || busy) return;
    setBusy(true);
    try {
      setDetail(
        await supportWrite<SupportTicket>(
          root + "/" + selected,
          value,
          "PATCH",
        ),
      );
      await load();
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  async function older() {
    if (!detail?.messages?.length || busy) return;
    setBusy(true);
    try {
      const result = await request<{
        items: SupportMessage[];
        has_older: boolean;
      }>(root + "/" + detail.id + "/messages?before=" + detail.messages[0].id);
      const height = scroll.current?.scrollHeight || 0;
      pinned.current = false;
      setDetail(
        (old) =>
          old && {
            ...old,
            messages: [...result.items, ...(old.messages || [])],
            has_older: result.has_older,
          },
      );
      requestAnimationFrame(() => {
        if (scroll.current)
          scroll.current.scrollTop = scroll.current.scrollHeight - height;
      });
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  async function olderNotes() {
    if (!detail?.notes?.length || busy) return;
    setBusy(true);
    try {
      const result = await request<{
        items: SupportMessage[];
        has_older: boolean;
      }>(root + "/" + detail.id + "/notes?before=" + detail.notes[0].id);
      setDetail(
        (old) =>
          old && {
            ...old,
            notes: [...result.items, ...(old.notes || [])],
            notes_has_older: result.has_older,
          },
      );
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  const closed = detail && ["resolved", "closed"].includes(detail.status);
  return (
    <div
      className={
        "support-panel" +
        (admin ? " support-admin" : "") +
        (selected || creating ? " has-conversation" : "")
      }
    >
      <aside className="support-list">
        {!admin && (
          <button
            onClick={() => {
              setCreating(true);
              setSelected(undefined);
              setDetail(undefined);
              setTitle("");
              setBody("");
            }}
          >
            {ui("Новое обращение")}
          </button>
        )}
        {admin && (
          <>
            <input
              aria-label={ui("Поиск обращений")}
              placeholder={ui("Имя, номер или текст…")}
              maxLength={160}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
            <select
              aria-label={ui("Статус обращений")}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">{ui("Все статусы")}</option>
              {Object.entries(supportStatuses()).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
            <select
              aria-label={ui("Категория обращений")}
              value={filterCategory}
              onChange={(e) => {
                setFilterCategory(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">{ui("Все категории")}</option>
              {Object.entries(supportCategories()).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </>
        )}
        {!tickets && <Loading />}
        {tickets?.length === 0 && <p>{ui("Обращений пока нет")}</p>}
        {tickets?.map((t) => (
          <button
            className={
              "support-ticket secondary" +
              (selected === t.id ? " selected" : "")
            }
            key={t.id}
            onClick={() => {
              setSelected(t.id);
              setCreating(false);
              if (detail?.id !== t.id) setDetail(undefined);
            }}
          >
            <small>
              №{t.number} ·{" "}
              {admin ? t.user_name : supportCategories()[t.category]}
              {t.unread > 0 && <b className="tag">{t.unread}</b>}
            </small>
            <strong>{t.title}</strong>
            <span>{t.last_message}</span>
            <small>
              {supportStatuses()[t.status]} · {dateTime(t.updated_at)}
            </small>
          </button>
        ))}
        <div className="support-pagination">
          <button
            className="text-button"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            {ui("Назад")}
          </button>
          <button
            className="text-button"
            disabled={next === null}
            onClick={() => setOffset(next || 0)}
          >
            {ui("Далее")}
          </button>
        </div>
      </aside>
      <section className="support-conversation">
        <Notice error={error} />
        {(selected || creating) && (
          <button
            className="text-button support-back"
            onClick={() => {
              setSelected(undefined);
              setCreating(false);
              setDetail(undefined);
            }}
          >
            {ui("← К обращениям")}
          </button>
        )}
        {creating ? (
          <>
            <h3>{ui("Новое обращение")}</h3>
            <label>
              {ui("Тема")}
              <input
                value={title}
                minLength={3}
                maxLength={160}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              {ui("Категория")}
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {Object.entries(supportCategories()).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : (
          detail && (
            <>
              <header>
                <h3>
                  №{detail.number} · {detail.title}
                </h3>
                <span className="tag">{supportStatuses()[detail.status]}</span>
                <small>{supportCategories()[detail.category]}</small>
              </header>
              <div className="support-controls">
                {canManage ? (
                  <>
                    <label>
                      {ui("Статус")}
                      <select
                        value={detail.status}
                        disabled={busy}
                        onChange={(e) =>
                          void change({ status: e.target.value })
                        }
                      >
                        {Object.entries(supportStatuses()).map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {ui("Исполнитель")}
                      <select
                        value={detail.assigned_to || ""}
                        disabled={busy}
                        onChange={(e) =>
                          void change({ assigned_to: e.target.value || null })
                        }
                      >
                        <option value="">{ui("Не назначен")}</option>
                        {operators.map((o) => (
                          <option value={o.id} key={o.id}>
                            {o.display_name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                ) : (
                  !admin && (
                    <button
                      className="text-button"
                      disabled={busy}
                      onClick={() =>
                        void change({ status: closed ? "open" : "closed" })
                      }
                    >
                      {closed
                        ? ui("Открыть повторно")
                        : ui("Закрыть обращение")}
                    </button>
                  )
                )}
              </div>
              <div
                className="support-messages"
                ref={scroll}
                role="log"
                aria-label={ui("Переписка")}
                onScroll={() => {
                  const el = scroll.current!;
                  pinned.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 60;
                  if (pinned.current) mark();
                }}
              >
                {detail.has_older && (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => void older()}
                  >
                    {ui("Предыдущие сообщения")}
                  </button>
                )}
                {detail.messages?.map((m) => (
                  <article
                    key={m.id}
                    className={
                      "support-message " +
                      (m.sender_kind === "system"
                        ? "system"
                        : m.sender_kind === "user"
                          ? "from-user"
                          : "from-staff")
                    }
                  >
                    {m.sender_kind !== "system" && (
                      <b>
                        {m.sender_kind === "staff" ? ui("Поддержка · ") : ""}
                        {m.author}
                      </b>
                    )}
                    <p>
                      {m.sender_kind === "system"
                        ? localizeSystem(m.body)
                        : m.body}
                    </p>
                    <time>{dateTime(m.created_at)}</time>
                  </article>
                ))}
              </div>
              {admin && (
                <details className="support-notes">
                  <summary>
                    {ui("Внутренние заметки · ")}
                    {detail.notes?.length || 0}
                  </summary>
                  {detail.notes_has_older && (
                    <button
                      className="text-button"
                      disabled={busy}
                      onClick={() => void olderNotes()}
                    >
                      {ui("Предыдущие заметки")}
                    </button>
                  )}
                  {detail.notes?.map((n) => (
                    <article key={n.id}>
                      <b>{n.author}</b>
                      <p>{n.body}</p>
                      <small>{dateTime(n.created_at)}</small>
                    </article>
                  ))}
                </details>
              )}
            </>
          )
        )}
        {selected && !detail && <Loading />}
        {!selected && !creating && (
          <div className="support-welcome">
            <Icon name="headset" size={40} />
            <h3>{ui("Мы рядом")}</h3>
            <p>
              {ui("Выберите обращение")}
              {!admin && ui(" или создайте новое")}
              {ui(". Переписка сохранится здесь.")}
            </p>
          </div>
        )}
        {(creating || (detail && canReply && (!closed || admin))) && (
          <form
            className="support-compose"
            onSubmit={(e) => {
              e.preventDefault();
              if (!closed || note) void send();
            }}
          >
            {admin && (
              <label>
                <input
                  type="checkbox"
                  checked={note}
                  onChange={(e) => setNote(e.target.checked)}
                />
                {ui("Внутренняя заметка — видна только сотрудникам")}
              </label>
            )}
            <textarea
              aria-label={note ? ui("Внутренняя заметка") : ui("Сообщение")}
              placeholder={
                note ? ui("Заметка для коллег…") : ui("Напишите сообщение…")
              }
              value={body}
              maxLength={4000}
              disabled={busy}
              onChange={(e) => setBody(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  if (!creating || title.trim().length >= 3) void send();
                }
              }}
            />
            <div>
              <small>
                {body.length}
                {ui("/4000 · Shift + Enter — новая строка")}
              </small>
              <button
                disabled={
                  busy ||
                  !body.trim() ||
                  Boolean(closed && !note) ||
                  (creating && title.trim().length < 3)
                }
              >
                {busy
                  ? ui("Отправляем…")
                  : creating
                    ? ui("Создать обращение")
                    : ui("Отправить")}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
