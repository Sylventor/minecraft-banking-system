import { localize as ui, useI18n as useLocale } from "../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  exchangeApi,
  exchangeStatus,
  type Stock,
  type Page,
  type Trade,
  type Candle,
  type Order,
} from "../exchange-api";
import { useBank } from "../bank-context";
import { Empty, Icon, Loading, Modal, PageHeading } from "../components/BankUI";
import { AccountSelect, Disclosure, Help } from "../components/Flow";
import { Notice } from "../components/Notice";
import { money, dateTime, normalizeAmount, localError } from "../i18n";
import { ApiFailure } from "../api";
import { cents, decimal } from "../money";
export function PriceChart({ items }: { items: Candle[] }) {
  useLocale();
  const [index, setIndex] = useState(0);
  if (!items.length)
    return <p>{ui("Нет сделок за период. Свечи без торгов не создаются.")}</p>;
  const low = items.reduce(
    (n, c) => (cents(c.low) < n ? cents(c.low) : n),
    cents(items[0].low),
  );
  const high = items.reduce(
    (n, c) => (cents(c.high) > n ? cents(c.high) : n),
    cents(items[0].high),
  );
  const y = (value: string) =>
    130 - Number(((cents(value) - low) * 11000n) / (high - low || 1n)) / 100;
  const c = items[Math.min(index, items.length - 1)];
  return (
    <div className="business-chart">
      <svg
        viewBox="0 0 600 150"
        role="img"
        aria-label={ui("Реальные OHLC котировки")}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          if (rect.width)
            setIndex(
              Math.max(
                0,
                Math.min(
                  items.length - 1,
                  Math.floor(
                    ((event.clientX - rect.left) / rect.width) * items.length,
                  ),
                ),
              ),
            );
        }}
      >
        {[25, 60, 95, 130].map((y) => (
          <line
            key={y}
            x1="10"
            x2="590"
            y1={y}
            y2={y}
            stroke="var(--border)"
            strokeDasharray="3 5"
          />
        ))}
        {items.map((c, i) => {
          const x = 10 + ((i + 0.5) * 580) / items.length,
            w = Math.max(1, Math.min(8, 450 / items.length));
          return (
            <g
              key={c.at}
              stroke={
                cents(c.close) >= cents(c.open)
                  ? "var(--chart-in)"
                  : "var(--chart-out)"
              }
            >
              <line x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} />
              <line
                strokeWidth={w}
                x1={x}
                x2={x}
                y1={y(c.open)}
                y2={y(c.close) + 0.5}
              />
            </g>
          );
        })}
      </svg>
      <label>
        {ui("Свеча")}
        <select
          value={Math.min(index, items.length - 1)}
          onChange={(e) => setIndex(Number(e.target.value))}
        >
          {items.map((c, i) => (
            <option value={i} key={c.at}>
              {dateTime(c.at)}
            </option>
          ))}
        </select>
      </label>
      <p className="chart-tooltip" role="status">
        {dateTime(c.at)}
        {ui(" · Открытие ")}
        {money(c.open)}
        {ui(" · Максимум ")}
        {money(c.high)}
        {ui(" · Минимум ")}
        {money(c.low)}
        {ui(" · Закрытие ")}
        {money(c.close)}
        {ui(" · Объём ")}
        {c.volume}
      </p>
    </div>
  );
}
export function Exchange() {
  useLocale();
  const { data, refresh, navigate, openReceipt } = useBank();
  const [stocks, setStocks] = useState<Page<Stock>>(),
    [selected, setSelected] = useState<Stock>(),
    [book, setBook] = useState<{
      bids: {
        price: string;
        quantity: number;
      }[];
      asks: {
        price: string;
        quantity: number;
      }[];
    }>(),
    [trades, setTrades] = useState<Page<Trade>>(),
    [candles, setCandles] = useState<Candle[]>([]);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState(""),
    [cursor, setCursor] = useState(""),
    [previous, setPrevious] = useState<string[]>([]),
    [period, setPeriod] = useState("24h"),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const [account, setAccount] = useState(data.primary_account.id),
    [side, setSide] = useState("BUY"),
    [kind, setKind] = useState("limit"),
    [quantity, setQuantity] = useState("1"),
    [price, setPrice] = useState(""),
    [budget, setBudget] = useState(""),
    [ipo, setIPO] = useState(false),
    [preview, setPreview] = useState<Record<string, unknown>>(),
    [result, setResult] = useState<
      | Order
      | {
          operation_id: string;
        }
    >();
  const [previewStock, setPreviewStock] = useState<Stock>();
  const [expires, setExpires] = useState(""),
    [uncertain, setUncertain] = useState(false);
  const executing = useRef(false);
  const detailSequence = useRef(0);
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(search);
      setCursor("");
      setPrevious([]);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);
  const activeTicker = useRef("");
  const sequence = useRef(0),
    attempt = useRef<
      | {
          body: string;
          key: string;
        }
      | undefined
    >(undefined);
  const load = useCallback(async () => {
    const n = ++sequence.current;
    setLoading(true);
    try {
      const list = await exchangeApi.get<Page<Stock>>("stocks", {
        q,
        cursor,
        limit: "25",
      });
      if (n === sequence.current) setStocks(list);
    } catch (e) {
      if (n === sequence.current) setError(localError(e));
    } finally {
      if (n === sequence.current) setLoading(false);
    }
  }, [q, cursor]);
  useEffect(() => {
    void load();
  }, [load]);
  const detail = useCallback(
    async (ticker: string) => {
      const n = ++detailSequence.current;
      try {
        const [s, b, t, c] = await Promise.all([
          exchangeApi.get<Stock>("stocks/" + ticker),
          exchangeApi.get<typeof book>("stocks/" + ticker + "/orderbook"),
          exchangeApi.get<Page<Trade>>("stocks/" + ticker + "/trades"),
          exchangeApi.get<{
            items: Candle[];
          }>("stocks/" + ticker + "/candles", { period }),
        ]);
        if (activeTicker.current !== ticker || n !== detailSequence.current)
          return;
        setSelected(s);
        setBook(b);
        setTrades(t);
        setCandles(c.items);
      } catch (e) {
        if (activeTicker.current === ticker && n === detailSequence.current)
          setError(localError(e));
      }
    },
    [period],
  );
  useEffect(() => {
    if (!selected) return;
    void detail(selected.ticker);
    const timer = setInterval(() => void detail(selected.ticker), 15000);
    return () => clearInterval(timer);
  }, [selected?.ticker, detail]);
  function prepare(isIPO = false) {
    try {
      if (!selected) return;
      if (!/^[1-9][0-9]{0,9}$/.test(quantity) || BigInt(quantity) > 1000000000n)
        throw new Error(
          ui("Количество должно быть целым от 1 до 1 000 000 000"),
        );
      const p = normalizeAmount(isIPO ? selected.ipo?.price || "" : price);
      if (!p) throw new Error(ui("Укажите положительную предельную цену"));
      const b = normalizeAmount(budget);
      if (!isIPO && kind === "market" && side === "BUY" && !b)
        throw new Error(ui("Для рыночной покупки задайте максимальный бюджет"));
      if (
        !isIPO &&
        kind === "limit" &&
        expires &&
        (!Number.isFinite(new Date(expires).getTime()) ||
          new Date(expires).getTime() <= Date.now() ||
          new Date(expires).getTime() > Date.now() + 90 * 86400000)
      )
        throw new Error(ui("Выберите срок в будущем, не более 90 дней."));
      setIPO(isIPO);
      setPreviewStock(selected);
      setPreview(
        isIPO
          ? { account_id: account, quantity: Number(quantity), confirmed: true }
          : {
              ticker: selected.ticker,
              account_id: account,
              quantity: Number(quantity),
              price: p,
              side,
              kind,
              ...(kind === "market" && side === "BUY" ? { budget: b } : {}),
              ...(kind === "limit" && expires
                ? { expires_at: new Date(expires).toISOString() }
                : {}),
              confirmed: true,
            },
      );
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function execute() {
    if (!preview || !previewStock || executing.current) return;
    executing.current = true;
    setBusy(true);
    try {
      const path = ipo ? `issuers/${previewStock.id}/ipo/buy` : "orders",
        body = JSON.stringify({ path, ...preview });
      if (attempt.current?.body !== body)
        attempt.current = { body, key: crypto.randomUUID() };
      setResult(await exchangeApi.write(path, preview, attempt.current.key));
      attempt.current = undefined;
      setPreview(undefined);
      setUncertain(false);
      await detail(previewStock.ticker);
      await refresh().catch(() =>
        setError(
          ui(
            "Заявка принята. Не удалось обновить баланс — обновите данные банка.",
          ),
        ),
      );
    } catch (e) {
      setUncertain(!(e instanceof ApiFailure) || e.status >= 500);
      setError(
        localError(e) +
          ui(
            " При потере ответа повторяйте то же подтверждение, не создавайте новую заявку.",
          ),
      );
    } finally {
      executing.current = false;
      setBusy(false);
    }
  }
  const maximum = preview
    ? ipo
      ? cents(previewStock?.ipo?.price || "0.00") *
        BigInt(String(preview.quantity))
      : preview.kind === "market" && preview.side === "BUY"
        ? cents(String(preview.budget))
        : cents(String(preview.price)) * BigInt(String(preview.quantity))
    : 0n;
  const amount = decimal;
  const sources = data.accounts.filter(
    (a) =>
      a.status === "active" &&
      a.permissions.includes("transfer") &&
      a.permissions.includes("exchange.trade") &&
      (a.account_type === "organization" || a.owner_id === data.profile.id),
  );
  const normalizedPrice = normalizeAmount(price),
    validQty =
      /^[1-9]\d{0,9}$/.test(quantity) && BigInt(quantity) <= 1000000000n;
  const estimated =
    normalizedPrice && validQty
      ? decimal(cents(normalizedPrice) * BigInt(quantity))
      : null;
  return (
    <>
      <PageHeading
        title={ui("Биржа")}
        subtitle={ui(
          "Компании вашего мира. Инвестируйте осознанно: цена акций может меняться.",
        )}
        action={
          <button className="secondary" onClick={() => navigate("investments")}>
            <Icon name="wallet" />
            {ui("Мой портфель")}
          </button>
        }
      />
      <Notice error={error} />
      <div className="exchange-toolbar">
        <label className="search-field">
          <Icon name="search" />
          <input
            aria-label={ui("Поиск компаний и тикеров")}
            placeholder={ui("Найдите компанию или тикер")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <button
          className="text-button"
          onClick={() => navigate("organizations")}
        >
          <Icon name="organization" />
          {ui("Выпустить акции компании")}
        </button>
      </div>
      {loading && !stocks ? (
        <Loading />
      ) : (
        <div className="stock-grid">
          {stocks?.items.map((s) => (
            <button
              className={
                "stock-card" + (selected?.id === s.id ? " selected" : "")
              }
              aria-label={s.ticker + " · " + s.name}
              aria-pressed={selected?.id === s.id}
              key={s.id}
              onClick={() => {
                if (s.ticker === selected?.ticker) return;
                setError("");
                setResult(undefined);
                setBook(undefined);
                setCandles([]);
                setTrades(undefined);
                setPrice(s.last_price || "");
                activeTicker.current = s.ticker;
                setSelected(s);
              }}
            >
              <span className="stock-identity">
                <span className="stock-monogram">{s.ticker.slice(0, 2)}</span>
                <span>
                  <b>{s.name}</b>
                  <small>{s.ticker}</small>
                </span>
              </span>
              <strong>
                {s.last_price ? money(s.last_price) : ui("Нет сделок")}
              </strong>
              <span className="stock-foot">
                <span>{exchangeStatus(s.status)}</span>
                <small>
                  {ui("Объём 24ч: ")}
                  {s.volume24}
                </small>
              </span>
            </button>
          ))}
        </div>
      )}
      {!stocks?.items.length && !loading && !error && (
        <section className="panel">
          <Empty
            icon="chart"
            title={ui("Пока нет доступных выпусков")}
            description={ui(
              "Здесь появятся компании после рассмотрения выпуска. Вы можете подготовить первый выпуск своей организации.",
            )}
            action={
              <button
                className="secondary"
                onClick={() => navigate("organizations")}
              >
                {ui("Перейти к организациям")}
              </button>
            }
          />
        </section>
      )}
      <div className="payment-pagination">
        <button
          className="secondary"
          disabled={!previous.length || loading}
          onClick={() => {
            setCursor(previous.at(-1) || "");
            setPrevious((p) => p.slice(0, -1));
          }}
        >
          {ui("Назад")}
        </button>
        <button
          className="secondary"
          disabled={!stocks?.next_cursor || loading}
          onClick={() => {
            setPrevious((p) => [...p, cursor]);
            setCursor(stocks?.next_cursor || "");
          }}
        >
          {ui("Далее")}
        </button>
        <button
          className="text-button"
          disabled={loading}
          onClick={() => void load()}
        >
          <Icon name="refresh" size={16} />
          {ui("Обновить")}
        </button>
      </div>
      {selected ? (
        <div className="exchange-layout">
          <div className="exchange-detail">
            <section className="panel">
              <div className="section-heading">
                <div className="stock-identity">
                  <span className="stock-monogram">
                    {selected.ticker.slice(0, 2)}
                  </span>
                  <div>
                    <h2>{selected.name}</h2>
                    <small>
                      {selected.ticker} · {exchangeStatus(selected.status)}
                    </small>
                  </div>
                </div>
                <strong className="stock-price">
                  {selected.last_price
                    ? money(selected.last_price)
                    : ui("Нет рыночной цены")}
                </strong>
              </div>
              <p className="field-hint">
                {selected.last_trade_at
                  ? ui("Последняя сделка ") + dateTime(selected.last_trade_at)
                  : ui("Цена появится после первой реальной сделки")}
              </p>
              <div className="stock-detail-metrics">
                <span>
                  {ui("24ч:")}{" "}
                  <b>
                    {selected.change_percent == null
                      ? "—"
                      : selected.change_percent + "%"}
                  </b>
                </span>
                <span>
                  {ui("Объём: ")}
                  <b>{selected.volume24}</b>
                </span>
                <span>
                  {ui("Сделок: ")}
                  <b>{selected.trades24 ?? "—"}</b>
                </span>
              </div>
              <label>
                {ui("Период")}
                <select
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                >
                  {Object.entries({
                    "1h": ui("1 час"),
                    "24h": ui("24 часа"),
                    "7d": ui("7 дней"),
                    "30d": ui("30 дней"),
                    "90d": ui("90 дней"),
                    all: ui("Всё время"),
                  }).map(([v, t]) => (
                    <option key={v} value={v}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              {!book ? (
                <Loading label={ui("Загружаем котировки…")} />
              ) : (
                <PriceChart items={candles} />
              )}
              <Help term={ui("Что показывает рыночная цена?")}>
                {ui(
                  "Цена последней реальной сделки. Следующая покупка или продажа может пройти по другой цене. Если сделок не было, рыночной оценки пока нет.",
                )}
              </Help>
              <Disclosure title={ui("О компании и условиях выпуска")}>
                <p>{selected.description}</p>
                <h3>{ui("Права акционеров")}</h3>
                <p>{selected.rights}</p>
                <h3>{ui("Условия")}</h3>
                <p>{selected.terms}</p>
              </Disclosure>
            </section>
            <section className="panel">
              <div className="section-heading">
                <h2>{ui("Предложения участников")}</h2>
                <span className="tag">{ui("Стакан")}</span>
              </div>
              <Help term={ui("Как читать стакан?")}>
                {ui(
                  "Слева — цены, по которым участники хотят купить акции, справа — продать. Количество показывает доступный объём по этой цене. Отсутствие предложений означает, что немедленная сделка может быть невозможна.",
                )}
              </Help>
              <div className="orderbook">
                {(["bids", "asks"] as const).map((key) => {
                  const levels = book?.[key] || [];
                  const maximumVolume = Math.max(
                    1,
                    ...levels.map((row) => row.quantity),
                  );
                  return (
                    <div
                      key={key}
                      className={
                        "book-side " + (key === "asks" ? "ask" : "bid")
                      }
                    >
                      <h3>
                        <span>
                          {key === "bids" ? ui("Покупают") : ui("Продают")}
                        </span>
                        <span>{ui("Акций")}</span>
                      </h3>
                      {levels.length ? (
                        levels.map((row) => (
                          <div className="book-level" key={row.price}>
                            <i
                              style={{
                                width:
                                  Math.max(
                                    2,
                                    (row.quantity / maximumVolume) * 100,
                                  ) + "%",
                              }}
                            />
                            <span>{money(row.price, undefined, false)}</span>
                            <span>{row.quantity}</span>
                          </div>
                        ))
                      ) : (
                        <p className="muted">
                          {book ? ui("Нет предложений") : ui("Загрузка…")}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
            <section className="panel">
              <h2>{ui("Последние сделки")}</h2>
              {!trades ? (
                <Loading />
              ) : !trades.items.length ? (
                <Empty
                  icon="history"
                  title={ui("Сделок пока нет")}
                  description={ui(
                    "Здесь появятся только фактически выполненные сделки.",
                  )}
                />
              ) : (
                <>
                  <div className="business-table desktop-records">
                    <table>
                      <thead>
                        <tr>
                          <th>{ui("Дата")}</th>
                          <th>{ui("Цена")}</th>
                          <th>{ui("Акций")}</th>
                          <th>{ui("Стоимость")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trades.items.map((t) => (
                          <tr key={t.id}>
                            <td>{dateTime(t.created_at)}</td>
                            <td>{money(t.price)}</td>
                            <td>{t.quantity}</td>
                            <td>{money(t.total)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="mobile-records">
                    {trades.items.map((t) => (
                      <article key={t.id} className="record-card">
                        <header>
                          <b>
                            {t.quantity}
                            {ui(" акций")}
                          </b>
                          <strong>{money(t.total)}</strong>
                        </header>
                        <small>
                          {dateTime(t.created_at)}
                          {ui(" · Цена ")}
                          {money(t.price)}
                        </small>
                      </article>
                    ))}
                  </div>
                </>
              )}
              {trades?.next_cursor && (
                <button
                  className="secondary"
                  onClick={async () => {
                    const ticker = selected.ticker;
                    try {
                      const next = await exchangeApi.get<Page<Trade>>(
                        `stocks/${ticker}/trades`,
                        { cursor: trades.next_cursor! },
                      );
                      if (activeTicker.current === ticker)
                        setTrades({
                          items: [...trades.items, ...next.items],
                          next_cursor: next.next_cursor,
                        });
                    } catch (e) {
                      setError(localError(e));
                    }
                  }}
                >
                  {ui("Ещё сделки")}
                </button>
              )}
            </section>
          </div>
          <section className="panel trade-ticket">
            <h2>{ui("Купить или продать")}</h2>
            <div className="segmented" aria-label={ui("Направление заявки")}>
              <button
                aria-pressed={side === "BUY"}
                onClick={() => setSide("BUY")}
              >
                {ui("Купить")}
              </button>
              <button
                aria-pressed={side === "SELL"}
                onClick={() => setSide("SELL")}
              >
                {ui("Продать")}
              </button>
            </div>
            <div className="business-filters">
              <AccountSelect
                label={ui("Счёт")}
                accounts={sources}
                value={account}
                onChange={setAccount}
              />
              {!sources.length && (
                <p className="info-box">
                  {ui(
                    "Для торговли нужен активный личный счёт или разрешение на торговлю и переводы со счёта организации.",
                  )}
                </p>
              )}
              <label>
                {ui("Количество")}
                <input
                  inputMode="numeric"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </label>
              <label>
                {side === "BUY"
                  ? ui("Максимальная цена, ДБ")
                  : ui("Минимальная цена, ДБ")}
                <input
                  inputMode="decimal"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                />
              </label>
              <small>
                {ui("За одну акцию.")}{" "}
                {side === "BUY"
                  ? ui("Вы не заплатите больше этой цены.")
                  : ui("Вы не продадите дешевле этой цены.")}
              </small>
              <Disclosure title={ui("Дополнительные параметры")}>
                <label>
                  {ui("Тип")}
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    <option value="limit">
                      {ui("По указанной цене или лучше")}
                    </option>
                    <option value="market">
                      {ui("Купить / продать сейчас в пределах цены")}
                    </option>
                  </select>
                </label>
                <Help term={ui("Лимитная и рыночная заявка")}>
                  {ui(
                    "Лимитная заявка может ждать подходящего предложения. Рыночная исполняется сейчас в заданных пределах, неисполненный остаток отменяется.",
                  )}
                </Help>
                {kind === "limit" && (
                  <label>
                    {ui("Срок действия")}
                    <input
                      type="datetime-local"
                      value={expires}
                      onChange={(e) => setExpires(e.target.value)}
                    />
                    <small>
                      {ui("По умолчанию 30 дней. Можно выбрать до 90 дней.")}
                    </small>
                  </label>
                )}
              </Disclosure>
              {kind === "market" && side === "BUY" && (
                <label>
                  {ui("Максимальный бюджет, ДБ")}
                  <input
                    inputMode="decimal"
                    value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                  />
                </label>
              )}
              <div className="business-preview">
                <span>
                  {side === "BUY"
                    ? ui("Стоимость в пределах цены")
                    : ui("Стоимость по минимальной цене")}
                </span>
                <h3>
                  {estimated
                    ? money(estimated)
                    : ui("Укажите цену и количество")}
                </h3>
                <small>
                  {ui("Комиссия: 0,00 ДБ.")}{" "}
                  {kind === "market"
                    ? ui("Неисполненный остаток отменяется.")
                    : ui(
                        "Возможны частичные исполнения. Средства или акции резервируются.",
                      )}
                </small>
              </div>
              <button
                disabled={
                  busy ||
                  selected.status !== "active" ||
                  !sources.some((a) => a.id === account)
                }
                onClick={() => prepare()}
              >
                {ui("Проверить заявку")}
                <Icon name="arrow" size={17} />
              </button>
              {selected.status !== "active" && (
                <p className="info-box">
                  {ui("Сейчас торги недоступны: ")}
                  {exchangeStatus(selected.status)}.
                </p>
              )}
            </div>
            {selected.ipo && (
              <div className="business-preview">
                <h3>{ui("Первичное размещение · IPO")}</h3>
                <p>
                  {money(selected.ipo.price)}
                  {ui(" за акцию · осталось")} {selected.ipo.remaining}
                </p>
                <small>
                  {dateTime(selected.ipo.starts_at)} —{" "}
                  {dateTime(selected.ipo.ends_at)}
                  {ui(". Лимит покупателя:")} {selected.ipo.max_per_buyer}.
                </small>
                <p className="muted">
                  {ui(
                    "Количество — из поля выше. Покупка проходит по фиксированной цене IPO.",
                  )}
                </p>
                <button
                  className="secondary"
                  disabled={
                    busy ||
                    selected.status !== "active" ||
                    selected.ipo.status !== "open" ||
                    !sources.some((a) => a.id === account)
                  }
                  onClick={() => prepare(true)}
                >
                  {ui("Купить на IPO")}
                </button>
              </div>
            )}
            <Help term={ui("Что такое зарезервированные средства?")}>
              {ui(
                "Это деньги или акции, выделенные для вашей заявки. Их нельзя потратить повторно, пока заявка не исполнена, не истекла или не отменена.",
              )}
            </Help>
            {result && (
              <div className="success-banner" role="status">
                <div>
                  <b>
                    {"status" in result
                      ? ui("Заявка принята: ") + exchangeStatus(result.status)
                      : ui("Акции куплены")}
                  </b>
                  <p>
                    <button
                      className="text-button"
                      onClick={() => navigate("investments")}
                    >
                      {ui("Портфель и заявки")}
                    </button>
                    {"operation_id" in result && (
                      <button
                        className="text-button"
                        onClick={() => openReceipt(result.operation_id)}
                      >
                        {ui("Квитанция")}
                      </button>
                    )}
                  </p>
                </div>
              </div>
            )}
          </section>
        </div>
      ) : stocks?.items.length ? (
        <section className="panel">
          <Empty
            icon="chart"
            title={ui("Выберите компанию")}
            description={ui(
              "Откройте карточку, чтобы посмотреть историю цены, предложения участников и купить акции.",
            )}
          />
        </section>
      ) : null}
      {preview && (
        <Modal
          title={ui("Подтвердите биржевую заявку")}
          onClose={() => {
            if (!busy && !uncertain) setPreview(undefined);
          }}
        >
          <Notice error={error} />
          <h3>
            {previewStock?.ticker} · {String(preview.quantity)}
            {ui(" акций ·")}{" "}
            {ipo
              ? "IPO"
              : preview.side === "BUY"
                ? ui("Покупка")
                : ui("Продажа")}
          </h3>
          <dl className="key-values">
            <div>
              <dt>{ui("Счёт")}</dt>
              <dd>
                {data.accounts.find((a) => a.id === preview.account_id)?.name}
              </dd>
            </div>
            <div>
              <dt>{ui("Цена за акцию")}</dt>
              <dd>
                {money(
                  ipo
                    ? previewStock?.ipo?.price || "0.00"
                    : String(preview.price),
                )}
              </dd>
            </div>
            <div>
              <dt>
                {ipo || preview.side === "BUY"
                  ? ui("Максимум списания")
                  : ui("Стоимость по минимальной цене")}
              </dt>
              <dd>{money(amount(maximum))}</dd>
            </div>
            <div>
              <dt>{ui("Комиссия")}</dt>
              <dd>{ui("0,00 ДБ")}</dd>
            </div>
          </dl>
          <p>
            {ipo
              ? ui("Покупка по фиксированной цене размещения.")
              : preview.kind === "market"
                ? ui(
                    "Рыночная цена может отличаться от последней сделки. Исполнение только в пределах бюджета и цены, остаток отменяется.",
                  )
                : ui(
                    "Деньги или акции резервируются; возможны частичные исполнения. Срок: ",
                  ) +
                  (preview.expires_at
                    ? dateTime(String(preview.expires_at))
                    : ui("30 дней")) +
                  "."}
          </p>
          <p className="info-box">
            {ui(
              "Цена и доходность не гарантированы. Продать акции получится только при наличии встречных предложений.",
            )}
          </p>
          {uncertain && (
            <p role="status">
              {ui(
                "Ответ пока не получен. Повторите это же подтверждение: банк проверит результат без создания второй заявки.",
              )}
            </p>
          )}
          <button disabled={busy} onClick={() => void execute()}>
            {busy ? ui("Проверяем…") : ui("Подтвердить")}
          </button>
        </Modal>
      )}
    </>
  );
}
