import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  businessApi,
  type AnalyticsPoint,
  type AnalyticsSummary,
  type BusinessOperation,
  type BusinessPage,
  type Category,
  type Counterparty,
} from "../../business-api";
import { Loading, Empty, PageHeading } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { Disclosure } from "../../components/Flow";
import {
  dateTime,
  localError,
  money,
  operationName,
  useI18n,
} from "../../i18n";
import type { Organization } from "../../types";
import { cents } from "../../money";
export { cents } from "../../money";
export function plotRatio(value: string, max: bigint): number {
  return max === 0n ? 0 : Number((cents(value) * 1000n) / max) / 10;
}
function Trend({
  points,
  kind,
  title,
}: {
  points: AnalyticsPoint[];
  kind: "flows" | "balance" | "activity";
  title: string;
}) {
  useLocale();
  const [focus, setFocus] = useState(0);
  const vals = points.flatMap((p) =>
    kind === "flows"
      ? [p.incoming, p.outgoing]
      : [kind === "balance" ? p.balance : `${p.activity}.00`],
  );
  const max = vals.reduce((a, v) => {
    const n = cents(v);
    return (n < 0n ? -n : n) > a ? (n < 0n ? -n : n) : a;
  }, 1n);
  function line(field: "incoming" | "outgoing" | "balance" | "activity") {
    return points
      .map(
        (p, i) =>
          `${10 + (points.length < 2 ? 0 : (i / (points.length - 1)) * 480)},${130 - plotRatio(field === "activity" ? `${p.activity}.00` : p[field], max)}`,
      )
      .join(" ");
  }
  const active = points[Math.min(focus, points.length - 1)];
  return (
    <section className="panel business-chart">
      <h2>{title}</h2>
      {!points.length ? (
        <Empty
          icon="history"
          title={ui("Нет данных")}
          description={ui("За выбранный период нет операций.")}
        />
      ) : (
        <>
          <svg
            viewBox="0 0 500 160"
            role="img"
            aria-label={title}
            onPointerMove={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              if (rect.width)
                setFocus(
                  Math.max(
                    0,
                    Math.min(
                      points.length - 1,
                      Math.round(
                        ((event.clientX - rect.left) / rect.width) *
                          (points.length - 1),
                      ),
                    ),
                  ),
                );
            }}
          >
            {[30, 65, 100, 130].map((y) => (
              <line
                key={y}
                x1="10"
                x2="490"
                y1={y}
                y2={y}
                stroke="var(--border)"
                strokeDasharray="3 5"
              />
            ))}
            <line
              x1={
                10 +
                (points.length < 2
                  ? 0
                  : Math.min(focus, points.length - 1) / (points.length - 1)) *
                  480
              }
              x2={
                10 +
                (points.length < 2
                  ? 0
                  : Math.min(focus, points.length - 1) / (points.length - 1)) *
                  480
              }
              y1="15"
              y2="130"
              stroke="var(--muted)"
            />
            {kind === "flows" ? (
              <>
                <polyline
                  points={line("incoming")}
                  fill="none"
                  stroke="var(--chart-in)"
                  strokeWidth="3"
                />
                <polyline
                  points={line("outgoing")}
                  fill="none"
                  stroke="var(--chart-out)"
                  strokeWidth="3"
                />
              </>
            ) : (
              <polyline
                points={line(kind === "balance" ? "balance" : "activity")}
                fill="none"
                stroke="var(--chart-price)"
                strokeWidth="3"
              />
            )}
          </svg>
          <label>
            {ui("Точка графика")}
            <select
              value={Math.min(focus, points.length - 1)}
              onChange={(e) => setFocus(Number(e.target.value))}
            >
              {points.map((p, i) => (
                <option key={p.at} value={i}>
                  {dateTime(p.at, undefined, false)}
                </option>
              ))}
            </select>
          </label>
          {active && (
            <p className="chart-tooltip" role="status">
              {dateTime(active.at, undefined, false)} ·{" "}
              {kind === "flows"
                ? uit(
                    "Поступления {0} / расходы {1}",
                    money(active.incoming),
                    money(active.outgoing),
                  )
                : kind === "balance"
                  ? money(active.balance)
                  : uit("{0} операций", active.activity)}
            </p>
          )}
        </>
      )}
    </section>
  );
}
const metricLabels = (): Record<string, string> => ({
  incoming: ui("Поступления"),
  outgoing: ui("Расходы"),
  net_cashflow: ui("Чистый денежный поток"),
  incoming_count: ui("Входящих операций"),
  outgoing_count: ui("Исходящих операций"),
  average_incoming: ui("Среднее поступление"),
  average_outgoing: ui("Средний расход"),
  largest_incoming: ui("Крупнейшее поступление"),
  largest_outgoing: ui("Крупнейший расход"),
});
export function Analytics({
  organization,
  permissions,
}: {
  organization: Organization;
  permissions: string[];
}) {
  const { t } = useI18n();
  const [period, setPeriod] = useState("30d"),
    [timezone, setTimezone] = useState(
      Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    ),
    [interval, setInterval] = useState("day");
  const [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [account, setAccount] = useState(""),
    [category, setCategory] = useState("");
  const [summary, setSummary] = useState<AnalyticsSummary>(),
    [points, setPoints] = useState<AnalyticsPoint[]>([]),
    [cats, setCats] = useState<Category[]>([]),
    [partners, setPartners] = useState<Counterparty[]>([]),
    [page, setPage] = useState<BusinessPage<BusinessOperation>>();
  const [cursor, setCursor] = useState(""),
    [back, setBack] = useState<string[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [exportType, setExportType] = useState("transactions"),
    [exportNext, setExportNext] = useState<string | null>(null);
  const [categoryName, setCategoryName] = useState("");
  const seq = useRef(0);
  useEffect(() => {
    setCursor("");
    setBack([]);
    setExportNext(null);
  }, [period, timezone, interval, account, category, start, end]);
  const params = useCallback(() => {
    const p = new URLSearchParams({
      period: period === "custom" ? "30d" : period,
      timezone,
      interval,
      limit: "25",
    });
    if (account) p.set("account_id", account);
    if (category) p.set("category", category);
    if (cursor) p.set("cursor", cursor);
    if (period === "custom" && start && end) {
      p.set("from", new Date(start).toISOString());
      p.set("to", new Date(end).toISOString());
    }
    return p;
  }, [period, timezone, interval, account, category, cursor, start, end]);
  const load = useCallback(async () => {
    const n = ++seq.current;
    setLoading(true);
    setError("");
    try {
      if (period === "custom" && (!start || !end))
        throw new Error(ui("Выберите обе границы периода"));
      const p = params();
      const [s, f, c, cp, tx] = await Promise.all([
        businessApi.analytics<AnalyticsSummary>(organization.id, "summary", p),
        businessApi.analytics<{
          items: AnalyticsPoint[];
        }>(organization.id, "cashflow", p),
        businessApi.analytics<{
          items: Category[];
        }>(organization.id, "categories", p),
        businessApi.analytics<{
          items: Counterparty[];
        }>(organization.id, "counterparties", p),
        businessApi.analytics<BusinessPage<BusinessOperation>>(
          organization.id,
          "transactions",
          p,
        ),
      ]);
      if (n === seq.current) {
        setSummary(s);
        setPoints(f.items);
        setCats(c.items);
        setPartners(cp.items);
        setPage(tx);
      }
    } catch (e) {
      if (n === seq.current)
        setError(
          e instanceof Error && !("code" in e) ? e.message : localError(e),
        );
    } finally {
      if (n === seq.current) setLoading(false);
    }
  }, [organization.id, params, period, start, end]);
  useEffect(() => {
    void load();
  }, [load]);
  async function exportData() {
    try {
      if (period === "custom" && (!start || !end))
        throw new Error(ui("Выберите обе границы периода"));
      const p = params();
      p.set("export_type", exportType);
      if (exportNext) p.set("cursor", exportNext);
      setExportNext(await businessApi.export(organization.id, p));
    } catch (e) {
      setError(e instanceof Error ? e.message : localError(e));
    }
  }
  const maxExpense = cats.reduce(
    (a, c) => (cents(c.outgoing) > a ? cents(c.outgoing) : a),
    1n,
  );
  return (
    <>
      <PageHeading
        title={ui("Аналитика")}
        subtitle={ui(
          "По подтверждённым банковским операциям. Денежный поток показывает движение средств, а не бухгалтерскую прибыль.",
        )}
      />
      <Notice error={error} />
      <section className="panel business-filters">
        <label>
          {ui("Период")}
          <select
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setCursor("");
              setBack([]);
              setExportNext(null);
            }}
          >
            {Object.entries({
              today: ui("Сегодня"),
              "7d": ui("7 дней"),
              "30d": ui("30 дней"),
              "90d": ui("90 дней"),
              month: ui("Текущий месяц"),
              previous_month: ui("Предыдущий месяц"),
              custom: ui("Свой диапазон"),
            }).map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          {ui("Счёт")}
          <select
            value={account}
            onChange={(e) => {
              setAccount(e.target.value);
              setCursor("");
              setBack([]);
              setExportNext(null);
            }}
          >
            <option value="">{ui("Все доступные счета")}</option>
            {organization.accounts?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <Disclosure title={ui("Дополнительные фильтры")}>
          <div className="business-filters">
            <label>
              {ui("Часовой пояс")}
              <input
                value={timezone}
                maxLength={64}
                onChange={(e) => setTimezone(e.target.value)}
              />
            </label>
            <label>
              {ui("Шаг")}
              <select
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
              >
                <option value="day">{ui("День")}</option>
                <option value="week">{ui("Неделя")}</option>
                <option value="month">{ui("Месяц")}</option>
              </select>
            </label>
            <label>
              {ui("Категория")}
              <select
                value={category}
                onChange={(e) => {
                  setCategory(e.target.value);
                  setCursor("");
                  setBack([]);
                }}
              >
                <option value="">{ui("Все категории")}</option>
                {cats.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </Disclosure>
        {period === "custom" && (
          <>
            <label>
              {ui("С (время вашего браузера)")}
              <input
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              {ui("До, не включая")}
              <input
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </>
        )}
        <button disabled={loading} onClick={() => void load()}>
          {ui("Обновить")}
        </button>
      </section>
      {loading && !summary ? (
        <Loading />
      ) : (
        summary && (
          <>
            <div className="business-metrics">
              <section className="panel">
                <span>{ui("Текущий баланс")}</span>
                <h2>{money(summary.balance)}</h2>
                <small>
                  {ui("Зарезервировано ")}
                  {money(summary.held)} · {summary.account_count}
                  {ui(" счетов")}
                </small>
              </section>
              {Object.entries(summary.current)
                .filter(([key]) =>
                  ["incoming", "outgoing", "net_cashflow"].includes(key),
                )
                .map(([key, value]) => (
                  <section className="panel" key={key}>
                    <span>{metricLabels()[key]}</span>
                    <h2>
                      {key.endsWith("count") ? value : money(String(value))}
                    </h2>
                    <small>
                      {ui("Предыдущий:")}{" "}
                      {key.endsWith("count")
                        ? summary.previous[key as keyof typeof summary.previous]
                        : money(
                            String(
                              summary.previous[
                                key as keyof typeof summary.previous
                              ],
                            ),
                          )}{" "}
                      {ui("· изменение ")}
                      {summary.changes[key]?.percent ?? "—"}%
                    </small>
                  </section>
                ))}
            </div>
            <Disclosure title={ui("Подробные финансовые показатели")}>
              <div className="business-metrics">
                {Object.entries(summary.current)
                  .filter(
                    ([key]) =>
                      !["incoming", "outgoing", "net_cashflow"].includes(key),
                  )
                  .map(([key, value]) => (
                    <section className="panel" key={key}>
                      <span>{metricLabels()[key]}</span>
                      <h2>
                        {key.endsWith("count") ? value : money(String(value))}
                      </h2>
                      <small>
                        {ui("Предыдущий:")}{" "}
                        {key.endsWith("count")
                          ? summary.previous[
                              key as keyof typeof summary.previous
                            ]
                          : money(
                              String(
                                summary.previous[
                                  key as keyof typeof summary.previous
                                ],
                              ),
                            )}{" "}
                        {ui("· изменение ")}
                        {summary.changes[key]?.percent ?? "—"}%
                      </small>
                    </section>
                  ))}
              </div>
            </Disclosure>
            <div className="business-grid">
              <Trend
                points={points}
                kind="flows"
                title={ui("Поступления и расходы")}
              />
              <Trend
                points={points}
                kind="balance"
                title={ui("Изменение остатка")}
              />
              <Trend
                points={points}
                kind="activity"
                title={ui("Активность операций")}
              />
              <section className="panel business-chart">
                <h2>{ui("Структура расходов")}</h2>
                {!cats.some((c) => cents(c.outgoing) > 0n) && (
                  <p className="muted">
                    {ui("За выбранный период расходов нет.")}
                  </p>
                )}
                {cats
                  .filter((c) => cents(c.outgoing) > 0n)
                  .map((c) => (
                    <div className="business-category-bar" key={c.key}>
                      <span>
                        {c.name} · {money(c.outgoing)}
                      </span>
                      <div
                        style={{
                          width: `${plotRatio(c.outgoing, maxExpense)}%`,
                        }}
                      />
                    </div>
                  ))}
              </section>
            </div>
            <section className="panel">
              <h2>{ui("Крупнейшие контрагенты")}</h2>
              <div className="business-table">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{ui("Контрагент / реквизиты")}</th>
                      <th scope="col">{ui("Направление")}</th>
                      <th scope="col">{ui("Операций")}</th>
                      <th scope="col">{ui("Объём")}</th>
                      <th scope="col">{ui("Последняя операция")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {partners.map((p, i) => (
                      <tr key={i}>
                        <td data-label={ui("Контрагент / реквизиты")}>
                          {p.name} · {p.public_number}
                        </td>
                        <td data-label={ui("Направление")}>
                          {p.direction === "incoming"
                            ? ui("Плательщик")
                            : ui("Получатель")}
                        </td>
                        <td data-label={ui("Операций")}>{p.count}</td>
                        <td data-label={ui("Объём")}>{money(p.volume)}</td>
                        <td data-label={ui("Последняя операция")}>
                          {dateTime(p.last_operation)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel">
              <h2>{ui("Операции")}</h2>
              <div className="business-table">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{ui("Дата")}</th>
                      <th scope="col">{ui("Назначение")}</th>
                      <th scope="col">{ui("Тип")}</th>
                      <th scope="col">{ui("Поток")}</th>
                      <th scope="col">{ui("Категория")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page?.items.map((x) => (
                      <tr key={x.id}>
                        <td data-label={ui("Дата")}>
                          {dateTime(x.created_at)}
                        </td>
                        <td data-label={ui("Назначение")}>{x.reason}</td>
                        <td data-label={ui("Тип")}>
                          {x.direction === "internal"
                            ? ui("Внутренний перевод")
                            : operationName(x.type, t)}
                        </td>
                        <td data-label={ui("Поток")}>{money(x.amount)}</td>
                        <td data-label={ui("Категория")}>
                          {permissions.includes("analytics.categorize") ? (
                            <select
                              aria-label={uit("Категория {0}", x.id)}
                              value={x.category}
                              onChange={async (e) => {
                                try {
                                  await businessApi.category(
                                    organization.id,
                                    x.id,
                                    e.target.value,
                                  );
                                  await load();
                                } catch (err) {
                                  setError(localError(err));
                                }
                              }}
                            >
                              {cats.map((c) => (
                                <option key={c.key} value={c.key}>
                                  {c.name}
                                </option>
                              ))}
                            </select>
                          ) : (
                            cats.find((c) => c.key === x.category)?.name ||
                            x.category
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!page?.items.length && <p>{ui("За период нет операций.")}</p>}
              <div className="payment-pagination">
                <button
                  className="secondary"
                  disabled={!back.length}
                  onClick={() => {
                    setCursor(back.at(-1) || "");
                    setBack((v) => v.slice(0, -1));
                  }}
                >
                  {ui("Назад")}
                </button>
                <button
                  className="secondary"
                  disabled={!page?.next_cursor}
                  onClick={() => {
                    setBack((v) => [...v, cursor]);
                    setCursor(page?.next_cursor || "");
                  }}
                >
                  {ui("Далее")}
                </button>
              </div>
              {permissions.includes("analytics.categorize") && (
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    try {
                      await businessApi.addCategory(
                        organization.id,
                        categoryName,
                      );
                      setCategoryName("");
                      await load();
                    } catch (err) {
                      setError(localError(err));
                    }
                  }}
                >
                  <label>
                    {ui("Своя категория")}
                    <input
                      value={categoryName}
                      maxLength={80}
                      onChange={(e) => setCategoryName(e.target.value)}
                      required
                    />
                  </label>
                  <button className="secondary">
                    {ui("Добавить категорию")}
                  </button>
                </form>
              )}
              {permissions.includes("analytics.export") && (
                <div className="business-export">
                  <label>
                    {ui("Экспорт")}
                    <select
                      value={exportType}
                      onChange={(e) => {
                        setExportType(e.target.value);
                        setExportNext(null);
                      }}
                    >
                      <option value="transactions">
                        {ui("История операций")}
                      </option>
                      <option value="cashflow">
                        {ui("Поступления и расходы")}
                      </option>
                      <option value="summary">{ui("Сводка")}</option>
                      {permissions.includes("payroll.read") && (
                        <option value="payroll">
                          {ui("Зарплатные выплаты")}
                        </option>
                      )}
                    </select>
                  </label>
                  <button onClick={() => void exportData()}>
                    {exportNext
                      ? ui("Экспорт следующей страницы CSV")
                      : ui("Экспорт CSV")}
                  </button>
                  <small>
                    {ui(
                      "UTF-8, до 100 записей на страницу; действуют текущие фильтры.",
                    )}
                  </small>
                </div>
              )}
            </section>
          </>
        )
      )}
    </>
  );
}
