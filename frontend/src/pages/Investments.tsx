import { localize as ui, useI18n as useLocale } from "../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  exchangeApi,
  exchangeStatus,
  type Page,
  type Position,
  type Order,
  type Trade,
  type Distribution,
} from "../exchange-api";
import { useBank } from "../bank-context";
import { Empty, Icon, Loading, Modal, PageHeading } from "../components/BankUI";
import { Help } from "../components/Flow";
import { MobileRecords, type MobileRecord } from "../components/MobileRecords";
import { Notice } from "../components/Notice";
import { money, dateTime, localError } from "../i18n";
export function Investments() {
  useLocale();
  const { data, refresh, openReceipt, navigate } = useBank();
  const [account, setAccount] = useState(data.primary_account.id),
    [tab, setTab] = useState("portfolio"),
    [cursor, setCursor] = useState(""),
    [previous, setPrevious] = useState<string[]>([]),
    [positions, setPositions] = useState<Page<Position>>(),
    [orders, setOrders] = useState<Page<Order>>(),
    [history, setHistory] = useState<Page<Trade>>(),
    [dividends, setDividends] = useState<Page<Distribution>>(),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [cancelling, setCancelling] = useState<Order>(),
    [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const n = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const params = { account_id: account, cursor, limit: "25" };
      if (tab === "portfolio") {
        const value = await exchangeApi.get<Page<Position>>(
          "portfolio",
          params,
        );
        if (n === sequence.current) setPositions(value);
      }
      if (tab === "orders") {
        const value = await exchangeApi.get<Page<Order>>("orders", {
          cursor,
          limit: "25",
        });
        if (n === sequence.current) setOrders(value);
      }
      if (tab === "history") {
        const value = await exchangeApi.get<Page<Trade>>(
          "portfolio/history",
          params,
        );
        if (n === sequence.current) setHistory(value);
      }
      if (tab === "dividends") {
        const value = await exchangeApi.get<Page<Distribution>>(
          "portfolio/dividend_history",
          params,
        );
        if (n === sequence.current) setDividends(value);
      }
    } catch (e) {
      if (n === sequence.current) setError(localError(e));
    } finally {
      if (n === sequence.current) setLoading(false);
    }
  }, [account, tab, cursor]);
  useEffect(() => {
    void load();
  }, [load]);
  const next =
    tab === "portfolio"
      ? positions?.next_cursor
      : tab === "orders"
        ? orders?.next_cursor
        : tab === "history"
          ? history?.next_cursor
          : dividends?.next_cursor;
  const accountName = (id: string) =>
    data.accounts.find((a) => a.id === id)?.name ||
    ui("Счёт закрыт или недоступен");
  const records: MobileRecord[] =
    tab === "portfolio"
      ? (positions?.items || []).map((p) => ({
          id: p.stock_id,
          title: p.ticker,
          amount: p.estimated_value
            ? money(p.estimated_value)
            : ui("Нет оценки"),
          subtitle: p.name,
          fields: [
            [ui("Акций"), p.quantity],
            [ui("Доступно / резерв"), `${p.available} / ${p.reserved}`],
            [ui("Средняя цена"), money(p.average_price)],
            [
              ui("Последняя цена"),
              p.last_price ? money(p.last_price) : ui("Нет сделок"),
            ],
            [
              ui("Нереализованный результат"),
              p.unrealized ? money(p.unrealized) : "—",
            ],
            [ui("Реализованный результат"), money(p.realized)],
            [ui("Дивиденды"), money(p.dividends)],
          ],
        }))
      : tab === "orders"
        ? (orders?.items || []).map((o) => ({
            id: o.id,
            title: o.side === "BUY" ? ui("Покупка") : ui("Продажа"),
            amount: exchangeStatus(o.status),
            subtitle: accountName(o.account_id),
            fields: [
              [ui("Цена"), money(o.price)],
              [ui("Остаток / всего"), `${o.remaining} / ${o.quantity}`],
              [ui("Срок"), dateTime(o.expires_at)],
            ],
            action: ["open", "partially_filled"].includes(o.status) ? (
              <button className="secondary" onClick={() => setCancelling(o)}>
                {ui("Отменить остаток")}
              </button>
            ) : undefined,
          }))
        : tab === "history"
          ? (history?.items || []).map((t) => ({
              id: t.id,
              title: `${t.ticker} · ${t.side === "BUY" ? ui("Покупка") : ui("Продажа")}`,
              amount: money(t.total),
              subtitle: dateTime(t.created_at),
              fields: [
                [ui("Акций"), t.quantity],
                [ui("Цена"), money(t.price)],
                [ui("Комиссия"), money(t.fee)],
              ],
              action: t.operation_id ? (
                <button
                  className="text-button"
                  onClick={() => openReceipt(t.operation_id!)}
                >
                  {ui("Квитанция")}
                </button>
              ) : undefined,
            }))
          : (dividends?.items || []).map((d) => ({
              id: d.id,
              title: d.ticker,
              amount: money(d.amount),
              subtitle: d.pay_at ? dateTime(d.pay_at) : ui("Дата не указана"),
              fields: [
                [ui("Акций на дату фиксации"), d.quantity],
                [ui("Статус"), exchangeStatus(d.status)],
              ],
            }));
  const currentAccount = data.accounts.find((a) => a.id === account);
  return (
    <>
      <PageHeading
        title={ui("Инвестиции")}
        subtitle={ui(
          "Портфели разделены по счетам. Оценка по последней сделке не гарантирует цену продажи.",
        )}
        action={
          <button className="secondary" onClick={() => navigate("exchange")}>
            <Icon name="chart" />
            {ui("На биржу")}
          </button>
        }
      />
      <Notice error={error} />
      <section className="panel">
        <label>
          {ui("Владелец и счёт портфеля")}
          <select
            value={account}
            onChange={(e) => {
              setAccount(e.target.value);
              setCursor("");
              setPrevious([]);
            }}
          >
            {data.accounts.map((a) => (
              <option value={a.id} key={a.id}>
                {a.organization_id ? ui("Организация · ") : ui("Личный · ")}
                {a.name} · {a.public_number}
              </option>
            ))}
          </select>
        </label>
        {currentAccount && (
          <div className="business-metrics">
            <section className="panel">
              <span>{ui("Деньги на счёте")}</span>
              <h2>{money(currentAccount.available.amount)}</h2>
              <small>{ui("Доступно для новых операций")}</small>
            </section>
            <section className="panel">
              <span>{ui("Резерв по счёту")}</span>
              <h2>{money(currentAccount.held.amount)}</h2>
              <small>{ui("Включает биржевые и другие резервы")}</small>
            </section>
          </div>
        )}
        <div className="payment-tabs">
          {Object.entries({
            portfolio: ui("Мой портфель"),
            orders: ui("Мои заявки"),
            history: ui("Сделки"),
            dividends: ui("Дивиденды"),
          }).map(([v, t]) => (
            <button
              key={v}
              className={tab === v ? "" : "secondary"}
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
        {loading ? (
          <Loading />
        ) : (
          <div className="business-table desktop-records">
            <table>
              {tab === "portfolio" ? (
                <>
                  <thead>
                    <tr>
                      <th>{ui("Акция")}</th>
                      <th>{ui("Всего / доступно / резерв")}</th>
                      <th>{ui("Средняя цена")}</th>
                      <th>{ui("Последняя цена")}</th>
                      <th>{ui("Оценка")}</th>
                      <th>{ui("Нереализованный / реализованный результат")}</th>
                      <th>{ui("Дивиденды")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions?.items.map((p) => (
                      <tr key={p.stock_id}>
                        <td>
                          {p.ticker} · {p.name}
                        </td>
                        <td>
                          {p.quantity} / {p.available} / {p.reserved}
                        </td>
                        <td>{money(p.average_price)}</td>
                        <td>
                          {p.last_price ? money(p.last_price) : ui("Нет цены")}
                          <small>
                            {p.last_trade_at ? dateTime(p.last_trade_at) : "—"}
                          </small>
                        </td>
                        <td>
                          {p.estimated_value ? money(p.estimated_value) : "—"}
                        </td>
                        <td>
                          {p.unrealized ? money(p.unrealized) : "—"} /{" "}
                          {money(p.realized)}
                        </td>
                        <td>{money(p.dividends)}</td>
                      </tr>
                    ))}
                  </tbody>
                </>
              ) : tab === "orders" ? (
                <>
                  <thead>
                    <tr>
                      <th>{ui("Счёт")}</th>
                      <th>{ui("Заявка")}</th>
                      <th>{ui("Цена")}</th>
                      <th>{ui("Остаток")}</th>
                      <th>{ui("Статус")}</th>
                      <th>{ui("Действие")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders?.items.map((o) => (
                      <tr key={o.id}>
                        <td>
                          {data.accounts.find((a) => a.id === o.account_id)
                            ?.public_number || ui("Счёт недоступен")}
                        </td>
                        <td>
                          {o.side === "BUY" ? ui("Покупка") : ui("Продажа")} ·{" "}
                          {o.kind === "market"
                            ? ui("Рыночная")
                            : ui("Лимитная")}
                        </td>
                        <td>{money(o.price)}</td>
                        <td>
                          {o.remaining} / {o.quantity}
                        </td>
                        <td>{exchangeStatus(o.status)}</td>
                        <td>
                          {["open", "partially_filled"].includes(o.status) && (
                            <button
                              className="text-button danger"
                              onClick={() => setCancelling(o)}
                            >
                              {ui("Отменить остаток")}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </>
              ) : tab === "history" ? (
                <>
                  <thead>
                    <tr>
                      <th>{ui("Дата")}</th>
                      <th>{ui("Акция / сторона")}</th>
                      <th>{ui("Количество")}</th>
                      <th>{ui("Цена")}</th>
                      <th>{ui("Стоимость")}</th>
                      <th>{ui("Квитанция")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history?.items.map((t) => (
                      <tr key={t.id}>
                        <td>{dateTime(t.created_at)}</td>
                        <td>
                          {t.ticker} ·{" "}
                          {t.side === "BUY" ? ui("Покупка") : ui("Продажа")}
                        </td>
                        <td>{t.quantity}</td>
                        <td>{money(t.price)}</td>
                        <td>{money(t.total)}</td>
                        <td>
                          {t.operation_id && (
                            <button
                              className="text-button"
                              onClick={() => openReceipt(t.operation_id!)}
                            >
                              {ui("Квитанция")}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </>
              ) : (
                <>
                  <thead>
                    <tr>
                      <th>{ui("Акция")}</th>
                      <th>{ui("Акций на дату фиксации")}</th>
                      <th>{ui("Сумма")}</th>
                      <th>{ui("Дата выплаты")}</th>
                      <th>{ui("Результат")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dividends?.items.map((d) => (
                      <tr key={d.id}>
                        <td>{d.ticker}</td>
                        <td>{d.quantity}</td>
                        <td>{money(d.amount)}</td>
                        <td>{d.pay_at ? dateTime(d.pay_at) : "—"}</td>
                        <td>{exchangeStatus(d.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </>
              )}
            </table>
          </div>
        )}
        {!loading && <MobileRecords items={records} />}
        {!loading && !error && records.length === 0 && (
          <Empty
            icon="chart"
            title={
              tab === "portfolio"
                ? ui("Ваш портфель начинается здесь")
                : ui("Пока нет записей")
            }
            description={
              tab === "portfolio"
                ? ui(
                    "Выберите компанию на бирже и изучите условия. Акции появятся после подтверждённой покупки.",
                  )
                : ui("Измените счёт или вернитесь после первой операции.")
            }
            action={
              <button
                className="secondary"
                onClick={() => navigate("exchange")}
              >
                {ui("Посмотреть компании")}
              </button>
            }
          />
        )}
        <Help
          term={
            tab === "orders"
              ? ui("Какие заявки здесь показаны?")
              : ui("Как читать портфель?")
          }
        >
          {tab === "orders"
            ? ui(
                "Все заявки, созданные вами, с указанием счёта. Отмена освобождает только резерв неисполненного остатка.",
              )
            : ui(
                "Оценка рассчитана по последней реальной сделке. Резерв — акции, выставленные на продажу. Дивиденды — выплаты компании её акционерам.",
              )}
        </Help>
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
      </section>
      {cancelling && (
        <Modal
          title={ui("Отменить неисполненный остаток?")}
          onClose={() => {
            if (!busy) setCancelling(undefined);
          }}
        >
          <Notice error={error} />
          <p>
            {cancelling.side} · {cancelling.remaining}
            {ui(" акций по")} {money(cancelling.price)}
            {ui(
              ". Резерв остатка будет освобождён, исполненные сделки не отменяются.",
            )}
          </p>
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await exchangeApi.write(
                  `orders/${cancelling.id}/cancel`,
                  { confirmed: true },
                  "cancel:" + cancelling.id,
                );
                setCancelling(undefined);
                await load();
                await refresh();
              } catch (e) {
                setError(localError(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {ui("Подтвердить отмену")}
          </button>
        </Modal>
      )}
    </>
  );
}
