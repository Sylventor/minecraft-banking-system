import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  exchangeApi,
  exchangeStatus,
  type Page,
  type Stock,
  type Order,
  type Trade,
} from "../../exchange-api";
import { Modal } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { localError, dateTime, money } from "../../i18n";
type Audit = {
  id: string;
  action: string;
  actor_id: string;
  resource_id: string;
  created_at: string;
  metadata: Record<string, unknown>;
};
type Issuance = {
  id: string;
  stock_id: string;
  quantity: number;
  actor_id: string;
  reason: string;
  created_at: string;
};
type Signal = {
  actor_id: string;
  stock_id: string;
  orders: number;
  cancellations: number;
  latest_order: string;
};
export function ExchangeAdmin({ permissions }: { permissions: string[] }) {
  useLocale();
  const [tab, setTab] = useState("issuers"),
    [cursor, setCursor] = useState(""),
    [previous, setPrevious] = useState<string[]>([]),
    [stocks, setStocks] = useState<Page<Stock>>(),
    [orders, setOrders] = useState<Page<Order>>(),
    [trades, setTrades] = useState<Page<Trade>>(),
    [audit, setAudit] = useState<Page<Audit>>(),
    [issues, setIssues] = useState<Page<Issuance>>(),
    [signals, setSignals] = useState<Signal[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<Stock>(),
    [status, setStatus] = useState("approved"),
    [reason, setReason] = useState(""),
    [quantity, setQuantity] = useState("");
  const attempt = useRef<
    | {
        body: string;
        key: string;
      }
    | undefined
  >(undefined);
  const editable = permissions.includes("admin.exchange.manage");
  const load = useCallback(async () => {
    try {
      const p = { cursor };
      if (tab === "issuers") setStocks(await exchangeApi.admin("issuers", p));
      if (tab === "orders") setOrders(await exchangeApi.admin("orders", p));
      if (tab === "trades") setTrades(await exchangeApi.admin("trades", p));
      if (tab === "audit") setAudit(await exchangeApi.admin("audit", p));
      if (tab === "issuances")
        setIssues(await exchangeApi.admin("issuances", p));
      if (tab === "surveillance")
        setSignals(
          (
            await exchangeApi.admin<{
              items: Signal[];
            }>("surveillance")
          ).items,
        );
    } catch (e) {
      setError(localError(e));
    }
  }, [tab, cursor]);
  useEffect(() => {
    void load();
  }, [load]);
  const next =
    tab === "issuers"
      ? stocks?.next_cursor
      : tab === "orders"
        ? orders?.next_cursor
        : tab === "trades"
          ? trades?.next_cursor
          : tab === "issuances"
            ? issues?.next_cursor
            : tab === "surveillance"
              ? null
              : audit?.next_cursor;
  async function submit() {
    if (!selected) return;
    setBusy(true);
    try {
      if (!reason.trim()) throw new Error(ui("Причина обязательна"));
      if (
        status === "additional" &&
        (!/^[1-9][0-9]{0,9}$/.test(quantity) || BigInt(quantity) > 1000000000n)
      )
        throw new Error(ui("Укажите корректное количество"));
      const body =
        status === "additional"
          ? { quantity: Number(quantity), reason, confirmed: true }
          : { status, reason, confirmed: true };
      const text = JSON.stringify({ id: selected.id, ...body });
      if (attempt.current?.body !== text)
        attempt.current = { body: text, key: crypto.randomUUID() };
      await exchangeApi.moderate(
        selected.id,
        body,
        attempt.current.key,
        status === "additional",
      );
      attempt.current = undefined;
      setSelected(undefined);
      await load();
    } catch (e) {
      setError((e as Error).message || localError(e));
    } finally {
      setBusy(false);
    }
  }
  const columnLabels =
    tab === "orders"
      ? [
          ui("ID / счёт"),
          ui("Сторона / тип"),
          ui("Количество / остаток"),
          ui("Цена"),
          ui("Статус"),
        ]
      : tab === "trades"
        ? [
            ui("Дата / ID"),
            ui("Покупатель → продавец"),
            ui("Акций"),
            ui("Цена"),
            ui("Стоимость / операция"),
          ]
        : tab === "issuances"
          ? [
              ui("Дата"),
              ui("Выпуск"),
              ui("Количество"),
              ui("Оператор"),
              ui("Причина"),
            ]
          : [
              ui("Дата"),
              ui("Действие"),
              ui("Оператор"),
              ui("Ресурс"),
              ui("Данные аудита"),
            ];
  return (
    <>
      <Notice error={error} />
      <p>
        {ui(
          "Самоторговля блокируется ядром. Аудит и история сделок неизменяемы. Изменить чужое владение вручную нельзя.",
        )}
      </p>
      <div className="payment-tabs">
        {Object.entries({
          issuers: ui("Эмитенты и модерация"),
          orders: ui("Все заявки"),
          trades: ui("Все сделки"),
          issuances: ui("Эмиссия акций"),
          audit: ui("Аудит / подозрительные повторы"),
          surveillance: ui("Паттерны активности"),
        }).map(([v, t]) => (
          <button
            className={tab === v ? "" : "secondary"}
            key={v}
            onClick={() => {
              setTab(v);
              setCursor("");
              setPrevious([]);
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "surveillance" ? (
        <section className="panel">
          <p>
            {ui(
              "Сигналы для проверки, не автоматическое обвинение: за 24 часа ≥10 заявок или ≥5 отмен на тикер.",
            )}
          </p>
          {signals.map((s) => (
            <p key={s.actor_id + s.stock_id}>
              {s.actor_id}
              {ui(" · выпуск ")}
              {s.stock_id}
              {ui(" · заявок ")}
              {s.orders}
              {ui(", отмен")} {s.cancellations} · {dateTime(s.latest_order)}
            </p>
          ))}
          {!signals.length && <p>{ui("Пороговых сигналов пока нет.")}</p>}
        </section>
      ) : tab === "issuers" ? (
        <div className="business-list">
          {stocks?.items.map((s) => (
            <article className="business-schedule" key={s.id}>
              <h3>
                {s.ticker} · {s.name} · {exchangeStatus(s.status)}
              </h3>
              <p>{s.description}</p>
              <p>
                {ui("Количество: ")}
                {s.total_quantity}
                {ui(" · Условия: ")}
                {s.terms}
                {ui(" · Права:")} {s.rights}
              </p>
              {editable && (
                <button
                  onClick={() => {
                    setSelected(s);
                    setStatus(
                      s.status === "pending_review"
                        ? "approved"
                        : s.status === "suspended"
                          ? "active"
                          : "suspended",
                    );
                    setReason("");
                    setQuantity("");
                  }}
                >
                  {ui("Рассмотреть / изменить статус")}
                </button>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="business-table">
          <table>
            <thead>
              <tr>
                {columnLabels.map((label) => (
                  <th scope="col" key={label}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tab === "orders"
                ? orders?.items.map((o) => (
                    <tr key={o.id}>
                      <td data-label={columnLabels[0]}>
                        {o.id}
                        <br />
                        {o.account_id}
                      </td>
                      <td data-label={columnLabels[1]}>
                        {o.side === "buy" ? ui("Покупка") : ui("Продажа")} /{" "}
                        {o.kind === "market" ? ui("Рыночная") : ui("Лимитная")}
                      </td>
                      <td data-label={columnLabels[2]}>
                        {o.quantity} / {o.remaining}
                      </td>
                      <td data-label={columnLabels[3]}>{money(o.price)}</td>
                      <td data-label={columnLabels[4]}>
                        {exchangeStatus(o.status)}
                      </td>
                    </tr>
                  ))
                : tab === "trades"
                  ? trades?.items.map((t) => (
                      <tr key={t.id}>
                        <td data-label={columnLabels[0]}>
                          {dateTime(t.created_at)}
                          <br />
                          {t.id}
                        </td>
                        <td data-label={columnLabels[1]}>
                          {ui("Детали связаны с заявками")}
                        </td>
                        <td data-label={columnLabels[2]}>{t.quantity}</td>
                        <td data-label={columnLabels[3]}>{money(t.price)}</td>
                        <td data-label={columnLabels[4]}>
                          {money(t.total)}
                          <br />
                          {t.operation_id}
                        </td>
                      </tr>
                    ))
                  : tab === "issuances"
                    ? issues?.items.map((i) => (
                        <tr key={i.id}>
                          <td data-label={columnLabels[0]}>
                            {dateTime(i.created_at)}
                          </td>
                          <td data-label={columnLabels[1]}>{i.stock_id}</td>
                          <td data-label={columnLabels[2]}>{i.quantity}</td>
                          <td data-label={columnLabels[3]}>{i.actor_id}</td>
                          <td data-label={columnLabels[4]}>{i.reason}</td>
                        </tr>
                      ))
                    : audit?.items.map((a) => (
                        <tr key={a.id}>
                          <td data-label={columnLabels[0]}>
                            {dateTime(a.created_at)}
                          </td>
                          <td data-label={columnLabels[1]}>{a.action}</td>
                          <td data-label={columnLabels[2]}>{a.actor_id}</td>
                          <td data-label={columnLabels[3]}>{a.resource_id}</td>
                          <td data-label={columnLabels[4]}>
                            {JSON.stringify(a.metadata)}
                          </td>
                        </tr>
                      ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="payment-pagination">
        <button
          className="secondary"
          disabled={!previous.length}
          onClick={() => {
            setCursor(previous.at(-1) || "");
            setPrevious((v) => v.slice(0, -1));
          }}
        >
          {ui("Назад")}
        </button>
        <button
          className="secondary"
          disabled={!next}
          onClick={() => {
            setPrevious((v) => [...v, cursor]);
            setCursor(next || "");
          }}
        >
          {ui("Далее")}
        </button>
        <button className="secondary" onClick={() => void load()}>
          {ui("Обновить")}
        </button>
      </div>
      {selected && (
        <Modal
          title={uit("Модерация {0}", selected.ticker)}
          onClose={() => {
            if (!busy) setSelected(undefined);
          }}
        >
          <Notice error={error} />
          <p>
            {selected.name} · {selected.total_quantity}
            {ui(" акций ·")} {exchangeStatus(selected.status)}
          </p>
          <label>
            {ui("Решение")}
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {Object.entries({
                approved: ui("Утвердить выпуск"),
                rejected: ui("Отклонить выпуск"),
                suspended: ui("Приостановить торги"),
                active: ui("Возобновить торги"),
                delisted: ui("Снять с торгов"),
                additional: ui("Дополнительная контролируемая эмиссия"),
              }).map(([v, t]) => (
                <option value={v} key={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          {status === "additional" && (
            <label>
              {ui("Дополнительно акций")}
              <input
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </label>
          )}
          <label>
            {ui("Причина")}
            <textarea
              required
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <p>
            {ui(
              "Изменение фиксируется в неизменяемом журнале. Действующие активы и сделки не удаляются.",
            )}
          </p>
          <button
            disabled={busy || !reason.trim()}
            onClick={() => void submit()}
          >
            {ui("Подтвердить решение")}
          </button>
          <button
            className="secondary"
            onClick={() => location.assign("/api/v1/auth/discord/login")}
          >
            {ui("Повторно подтвердить личность через Discord")}
          </button>
        </Modal>
      )}
    </>
  );
}
