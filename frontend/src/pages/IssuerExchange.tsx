import { localize as ui, useI18n as useLocale } from "../i18n";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  exchangeApi,
  exchangeStatus,
  type Page,
  type Stock,
  type Dividend,
} from "../exchange-api";
import { useBank } from "../bank-context";
import { Empty, Loading, Modal } from "../components/BankUI";
import { Disclosure, Help } from "../components/Flow";
import { IssuerWizard, savedIPOPlan } from "./IssuerWizard";
import { Notice } from "../components/Notice";
import { dateTime, localError, money, normalizeAmount } from "../i18n";
import type { Organization } from "../types";
export function IssuerExchange({
  organization,
  permissions,
}: {
  organization: Organization;
  permissions: string[];
}) {
  useLocale();
  const { data, refresh, navigate } = useBank();
  const [stocks, setStocks] = useState<Page<Stock>>(),
    [dividends, setDividends] = useState<Page<Dividend>>(),
    [stockCursor, setStockCursor] = useState(""),
    [dividendCursor, setDividendCursor] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [mode, setMode] = useState("overview"),
    [stock, setStock] = useState(""),
    [values, setValues] = useState<Record<string, string>>({
      account_id: organization.accounts?.[0]?.id || "",
    }),
    [preview, setPreview] = useState<{
      path: string;
      body: Record<string, unknown>;
      dividend?: Dividend;
    }>();
  const attempt = useRef<
    | {
        body: string;
        key: string;
      }
    | undefined
  >(undefined);
  const canIssue = permissions.includes("exchange.issuer.manage"),
    canDividend = permissions.includes("exchange.dividends.manage");
  const load = useCallback(async () => {
    try {
      if (canIssue || canDividend)
        setStocks(
          await exchangeApi.get("issuers", {
            organization_id: organization.id,
            cursor: stockCursor,
          }),
        );
      if (canDividend)
        setDividends(
          await exchangeApi.get("dividends", {
            organization_id: organization.id,
            cursor: dividendCursor,
          }),
        );
    } catch (e) {
      setError(localError(e));
    }
  }, [organization.id, canIssue, canDividend, stockCursor, dividendCursor]);
  useEffect(() => {
    void load();
  }, [load]);
  function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const body: Record<string, unknown> = {
        confirmed: true,
        account_id: values.account_id,
      };
      let path = "";
      const quantity = () => {
        if (
          !/^[1-9][0-9]{0,9}$/.test(values.quantity || "") ||
          BigInt(values.quantity) > 1000000000n
        )
          throw new Error(ui("Некорректное целое количество акций"));
        return Number(values.quantity);
      };
      const amount = (field: string) => {
        const n = normalizeAmount(values[field] || "");
        if (!n) throw new Error(ui("Укажите положительную сумму"));
        return n;
      };
      const date = (field: string) => new Date(values[field]).toISOString();
      if (mode === "ipo") {
        path = `issuers/${stock}/ipo`;
        delete body.account_id;
        Object.assign(body, {
          quantity: quantity(),
          price: amount("price"),
          starts_at: date("starts_at"),
          ends_at: date("ends_at"),
          max_per_buyer: values.max_per_buyer
            ? Number(values.max_per_buyer)
            : 1000000000,
        });
      }
      if (mode === "dividend") {
        path = "dividends";
        Object.assign(body, {
          stock_id: stock,
          mode: values.dividend_mode || "total",
          amount: amount("amount"),
          record_at: date("record_at"),
          pay_at: date("pay_at"),
        });
      }
      setPreview({ path, body });
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function execute() {
    if (!preview) return;
    setBusy(true);
    try {
      const body = JSON.stringify(preview);
      if (attempt.current?.body !== body)
        attempt.current = { body, key: crypto.randomUUID() };
      await exchangeApi.write(preview.path, preview.body, attempt.current.key);
      attempt.current = undefined;
      setPreview(undefined);
      await load();
      await refresh();
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  const labels: Record<string, string> = {
    mode: ui("Распределение"),
    ticker: ui("Тикер (2–10 латинских заглавных букв/цифр)"),
    name: ui("Название выпуска"),
    description: ui("Описание деятельности"),
    terms: ui("Условия размещения"),
    rights: ui("Права акционеров"),
    quantity: ui("Количество акций"),
    price: ui("Цена IPO, ДБ"),
    max_per_buyer: ui("Максимум одному покупателю"),
    starts_at: ui("Начало IPO"),
    ends_at: ui("Окончание IPO"),
    amount: ui("Дивиденды, ДБ"),
    record_at: ui("Дата фиксации владельцев"),
    pay_at: ui("Дата выплаты"),
  };
  const fields =
    mode === "ipo"
      ? ["quantity", "price", "max_per_buyer", "starts_at", "ends_at"]
      : ["amount", "record_at", "pay_at"];
  function viewIssues(id: string) {
    if (id === organization.id) setMode("overview");
    else {
      navigate("organizations");
      location.hash = "/organizations?organization_id=" + id + "&tab=exchange";
    }
  }
  if (mode === "issuer" && canIssue)
    return (
      <section className="panel">
        <div className="section-row">
          <h2>{ui("Новый выпуск акций")}</h2>
          <button className="secondary" onClick={() => setMode("overview")}>
            {ui("К выпускам")}
          </button>
        </div>
        <IssuerWizard
          organization={organization}
          permissions={permissions}
          onSubmitted={load}
          onViewIssues={viewIssues}
        />
      </section>
    );
  return (
    <section className="panel">
      <h2>{ui("Акции организации")}</h2>
      <Notice error={error} />
      <p>
        {ui(
          "Акции не дают доступ к банковским счетам или управлению организацией. Выпуск утверждает отдельный администратор.",
        )}
      </p>
      <div className="business-list">
        {!stocks && !error && <Loading />}
        {stocks?.items.length === 0 && (
          <Empty
            icon="chart"
            title={ui("Первый выпуск вашей компании")}
            description={ui(
              "Пройдите пошаговый мастер: он рассчитает доли и план IPO, а затем отправит выпуск на рассмотрение.",
            )}
          />
        )}
        {stocks?.items.map((s) => (
          <article className="business-schedule" key={s.id}>
            <h3>
              {s.ticker} · {s.name} · {exchangeStatus(s.status)}
            </h3>
            <p>
              {ui("Утверждаемая эмиссия: ")}
              {s.total_quantity}
            </p>
            <small>
              {s.created_at ? ui("Создан ") + dateTime(s.created_at) : ""}
            </small>
            {s.status === "pending_review" && (
              <p className="info-box">
                {ui(
                  "Выпуск на рассмотрении. После утверждения можно будет открыть IPO.",
                )}
              </p>
            )}
            {s.status === "rejected" && (
              <p className="error">
                {ui("Причина отказа:")}{" "}
                {s.reason || ui("Обратитесь к администратору за уточнением")}
              </p>
            )}
            <Disclosure title={ui("Условия выпуска")}>
              <p>{s.description}</p>
              <p>{s.terms}</p>
              <p>{s.rights}</p>
            </Disclosure>
            {canIssue && s.status === "approved" && (
              <button
                className="secondary"
                onClick={() => {
                  setStock(s.id);
                  setMode("ipo");
                  setValues({ ...savedIPOPlan(data.profile.id, s.id) });
                }}
              >
                {ui("Настроить IPO")}
              </button>
            )}
            {s.status === "draft" && canIssue && (
              <button
                onClick={() =>
                  setPreview({
                    path: `issuers/${s.id}/submit`,
                    body: { confirmed: true },
                  })
                }
              >
                {ui("Отправить на модерацию")}
              </button>
            )}
          </article>
        ))}
      </div>
      {stocks?.next_cursor && (
        <button
          className="secondary"
          onClick={() => setStockCursor(stocks.next_cursor!)}
        >
          {ui("Следующие выпуски")}
        </button>
      )}
      <div className="payment-tabs">
        {canIssue && (
          <>
            <button
              className={mode === "issuer" ? "" : "secondary"}
              onClick={() => setMode("issuer")}
            >
              {ui("Новый выпуск")}
            </button>
            <button
              className={mode === "ipo" ? "" : "secondary"}
              onClick={() => setMode("ipo")}
            >
              {ui("Первичное размещение")}
            </button>
          </>
        )}
        {canDividend && (
          <button
            className={mode === "dividend" ? "" : "secondary"}
            onClick={() => setMode("dividend")}
          >
            {ui("Объявить дивиденды")}
          </button>
        )}
      </div>
      {(mode === "ipo" || mode === "dividend") && (
        <form onSubmit={submit}>
          <label>
            {ui("Выпуск")}
            <select
              value={stock}
              onChange={(e) => setStock(e.target.value)}
              required
            >
              <option value="">{ui("Выберите выпуск")}</option>
              {stocks?.items.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.ticker} · {exchangeStatus(s.status)}
                </option>
              ))}
            </select>
          </label>
          {mode !== "ipo" && (
            <label>
              {ui("Счёт организации")}
              <select
                value={values.account_id || ""}
                onChange={(e) =>
                  setValues((v) => ({ ...v, account_id: e.target.value }))
                }
                required
              >
                {organization.accounts?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.public_number}
                  </option>
                ))}
              </select>
            </label>
          )}
          {mode === "dividend" && (
            <label>
              {ui("Распределение")}
              <select
                value={values.dividend_mode || "total"}
                onChange={(e) =>
                  setValues((v) => ({ ...v, dividend_mode: e.target.value }))
                }
              >
                <option value="total">{ui("Общая сумма")}</option>
                <option value="per_share">{ui("Сумма на одну акцию")}</option>
              </select>
            </label>
          )}
          <div className="business-filters">
            {fields.map((f) => (
              <label key={f}>
                {labels[f]}
                <input
                  required={f !== "max_per_buyer"}
                  type={f.endsWith("_at") ? "datetime-local" : "text"}
                  maxLength={
                    ["terms", "description", "rights"].includes(f) ? 2000 : 120
                  }
                  value={values[f] || ""}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [f]: e.target.value }))
                  }
                />
              </label>
            ))}
          </div>
          <p>
            {ui(
              "Даты вводятся в часовом поясе браузера. Дивиденды фиксируются по реальному журналу владения на указанную дату; утверждение возможно после этой даты.",
            )}
          </p>
          {mode === "ipo" && (
            <Help term={ui("Как открыть первичное размещение?")}>
              {ui(
                "IPO — первая продажа акций компании по фиксированной цене. Сначала выпуск должен быть утверждён. Проверьте предложенные в мастере количество, цену и даты: продажи откроются после этого подтверждения.",
              )}
            </Help>
          )}
          <button
            disabled={
              busy ||
              (mode !== "dividend" && !canIssue) ||
              (mode === "dividend" && !canDividend)
            }
          >
            {ui("Проверить и подтвердить")}
          </button>
        </form>
      )}
      <h3>{ui("Дивидендные ведомости")}</h3>
      {dividends?.items.map((d) => (
        <article className="business-schedule" key={d.id}>
          <p>
            {exchangeStatus(d.status)} · {money(d.total)} · {dateTime(d.pay_at)}
          </p>
          <p>
            {ui("Остаток округления: ")}
            {money(d.rounding_remainder)}
            {ui(". Получателей:")} {d.items.length}
          </p>
          <div className="business-table">
            <table>
              <thead>
                <tr>
                  <th scope="col">{ui("Счёт")}</th>
                  <th scope="col">{ui("Акций на дату фиксации")}</th>
                  <th scope="col">{ui("Сумма")}</th>
                  <th scope="col">{ui("Статус")}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((i) => (
                  <tr key={i.id}>
                    <td data-label={ui("Счёт")}>{i.account_id}</td>
                    <td data-label={ui("Акций на дату фиксации")}>
                      {i.quantity}
                    </td>
                    <td data-label={ui("Сумма")}>{money(i.amount)}</td>
                    <td data-label={ui("Статус")}>
                      {exchangeStatus(i.status)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.status === "draft" &&
            canDividend &&
            (d.created_by !== data.profile.id ||
              organization.owner_id === data.profile.id) && (
              <button
                onClick={() =>
                  setPreview({
                    path: `dividends/${d.id}/approve`,
                    body: { confirmed: true },
                    dividend: d,
                  })
                }
              >
                {ui("Утвердить снимок получателей")}
              </button>
            )}
          {d.status === "approved" && (
            <button
              onClick={() =>
                setPreview({
                  path: `dividends/${d.id}/execute`,
                  body: { confirmed: true },
                  dividend: d,
                })
              }
            >
              {ui("Выплатить дивиденды")}
            </button>
          )}
        </article>
      ))}
      {dividends?.next_cursor && (
        <button
          className="secondary"
          onClick={() => setDividendCursor(dividends.next_cursor!)}
        >
          {ui("Следующие ведомости")}
        </button>
      )}
      <button className="secondary" onClick={() => void load()}>
        {ui("Обновить")}
      </button>
      {preview && (
        <Modal
          title={ui("Подтверждение действия эмитента")}
          onClose={() => {
            if (!busy) setPreview(undefined);
          }}
        >
          <Notice error={error} />
          <p>
            {preview.path.endsWith("/ipo")
              ? ui("Открыть первичное размещение акций")
              : preview.path.endsWith("/submit")
                ? ui("Отправить выпуск на рассмотрение")
                : preview.path.endsWith("/approve")
                  ? ui("Утвердить список получателей дивидендов")
                  : preview.path.endsWith("/execute")
                    ? ui("Выплатить дивиденды")
                    : ui("Создать дивидендную ведомость")}
          </p>
          {preview.dividend && (
            <div className="business-preview">
              <p>
                {ui("Сумма декларации: ")}
                {money(preview.dividend.amount)} ·{" "}
                {preview.dividend.mode === "total"
                  ? ui("общая")
                  : ui("на акцию")}
              </p>
              <p>
                {ui("Фиксация ")}
                {dateTime(preview.dividend.record_at)}
                {ui(" · выплата")} {dateTime(preview.dividend.pay_at)}
              </p>
              <p>
                {ui("Зафиксировано к выплате: ")}
                {money(preview.dividend.total)}
                {ui(" · комиссия 0,00 ДБ · остаток округления")}{" "}
                {money(preview.dividend.rounding_remainder)}
              </p>
              {preview.dividend.items.map((i) => (
                <p key={i.id}>
                  {i.account_id} · {i.quantity}
                  {ui(" акций · ")}
                  {money(i.amount)} · {exchangeStatus(i.status)}
                </p>
              ))}
              {preview.dividend.status === "draft" && (
                <p>
                  {ui(
                    "Утверждение рассчитает и зафиксирует получателей по журналу на дату фиксации. После него проверьте полный список перед отдельным подтверждением выплаты.",
                  )}
                </p>
              )}
            </div>
          )}
          {Object.entries(preview.body)
            .filter(
              ([k]) =>
                ![
                  "confirmed",
                  "organization_id",
                  "stock_id",
                  "account_id",
                ].includes(k),
            )
            .map(([k, v]) => (
              <p key={k}>
                {labels[k] || k}:{" "}
                {k.endsWith("_at")
                  ? dateTime(String(v))
                  : ["amount", "price"].includes(k)
                    ? money(String(v))
                    : k === "mode"
                      ? v === "total"
                        ? ui("Общая сумма")
                        : ui("На одну акцию")
                      : String(v)}
              </p>
            ))}
          {Boolean(preview.body.account_id) && (
            <p>
              {ui("Счёт:")}{" "}
              {organization.accounts?.find(
                (a) => a.id === preview.body.account_id,
              )?.name || ui("Выбранный счёт организации")}
            </p>
          )}
          <p>
            {ui(
              "Проверьте условия перед подтверждением. При потере ответа повторите это же действие — банк проверит результат без дублирования.",
            )}
          </p>
          <button disabled={busy} onClick={() => void execute()}>
            {ui("Подтвердить")}
          </button>
        </Modal>
      )}
    </section>
  );
}
