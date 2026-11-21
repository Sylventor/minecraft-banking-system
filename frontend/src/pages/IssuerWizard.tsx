import {
  languageLocale,
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../i18n";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiFailure } from "../api";
import { businessApi } from "../business-api";
import { useBank } from "../bank-context";
import { Empty, Icon, Loading } from "../components/BankUI";
import { AccountSelect, Disclosure, Help, Steps } from "../components/Flow";
import { Notice } from "../components/Notice";
import { exchangeApi, exchangeStatus, type Stock } from "../exchange-api";
import { dateTime, localError, money, normalizeAmount } from "../i18n";
import { cents, decimal } from "../money";
import type { Organization } from "../types";
type Plan = {
  organization_id: string;
  account_id: string;
  ticker: string;
  name: string;
  description: string;
  quantity: string;
  ipo_quantity: string;
  price: string;
  starts_at: string;
  ends_at: string;
  max_per_buyer: string;
  terms: string;
  rights: string;
};
type Attempt = {
  key: string;
  submitKey: string;
  body: Record<string, unknown>;
  stockId?: string;
};
const labels = () => [
  ui("Организация"),
  ui("Компания"),
  ui("Акции"),
  ui("Цена"),
  ui("Условия"),
  ui("Обзор"),
  ui("Отправка"),
];
function initial(org: Organization): Plan {
  return {
    organization_id: org.id,
    account_id: org.accounts?.find((a) => a.status === "active")?.id || "",
    ticker: "",
    name: org.name,
    description: "",
    quantity: "1000",
    ipo_quantity: "100",
    price: "",
    starts_at: "",
    ends_at: "",
    max_per_buyer: "",
    terms: "",
    rights: "",
  };
}
function restore(
  key: string,
  org: Organization,
): {
  plan: Plan;
  step: number;
  attempt?: Attempt;
} {
  const empty = { plan: initial(org), step: 0 };
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (!value || value.version !== 1 || !value.plan) return empty;
    const plan = initial(org);
    for (const field of Object.keys(plan) as (keyof Plan)[])
      if (typeof value.plan[field] === "string")
        plan[field] = value.plan[field].slice(0, 4000);
    const attempt =
      value.attempt &&
      typeof value.attempt.key === "string" &&
      typeof value.attempt.submitKey === "string" &&
      typeof value.attempt.body === "object" &&
      value.attempt.body !== null
        ? (value.attempt as Attempt)
        : undefined;
    return {
      plan,
      step: attempt ? 6 : Math.max(0, Math.min(5, Number(value.step) || 0)),
      attempt,
    };
  } catch {
    return empty;
  }
}
export function savedIPOPlan(
  user: string,
  stock: string,
): Record<string, string> {
  try {
    const value = JSON.parse(
      localStorage.getItem(`horizon.ipo-plan.${user}.${stock}`) || "{}",
    );
    if (!value || typeof value !== "object") return {};
    return Object.fromEntries(
      ["quantity", "price", "starts_at", "ends_at", "max_per_buyer"]
        .filter((key) => typeof value[key] === "string")
        .map((key) => [key, value[key]]),
    );
  } catch {
    return {};
  }
}
export function IssuerWizard({
  organization,
  permissions,
  onSubmitted,
  onViewIssues,
}: {
  organization: Organization;
  permissions: string[];
  onSubmitted: () => Promise<void>;
  onViewIssues?: (organizationId: string) => void;
}) {
  useLocale();
  const { data } = useBank();
  const key = "horizon.issuer-draft." + data.profile.id;
  const [restored] = useState(() => restore(key, organization));
  const [plan, setPlan] = useState(restored.plan),
    [step, setStep] = useState(restored.step),
    [attempt, setAttempt] = useState<Attempt | undefined>(restored.attempt);
  const [org, setOrg] = useState<Organization | undefined>(
      restored.plan.organization_id === organization.id
        ? organization
        : undefined,
    ),
    [orgs, setOrgs] = useState<Organization[]>([organization]),
    [grants, setGrants] = useState(permissions);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [storageWarning, setStorageWarning] = useState(""),
    [result, setResult] = useState<Stock>(),
    [consent, setConsent] = useState(false);
  const [availability, setAvailability] = useState<{
    ticker: string;
    available: boolean;
  }>();
  const sending = useRef(false);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panel.current?.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [step, result?.id]);
  useEffect(() => {
    let active = true;
    api
      .organizations()
      .then((value) => {
        if (active) setOrgs(value);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    setOrg(undefined);
    Promise.all([
      api.organization(plan.organization_id),
      businessApi.permissions(plan.organization_id),
    ])
      .then(([organization, p]) => {
        if (active) {
          setOrg(organization);
          setGrants(p.permissions);
        }
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, [plan.organization_id]);
  function persist(value: Plan, nextStep: number, pending?: Attempt) {
    try {
      localStorage.setItem(
        key,
        JSON.stringify({
          version: 1,
          plan: value,
          step: nextStep,
          attempt: pending,
        }),
      );
    } catch {
      setStorageWarning(
        ui(
          "Браузер не разрешил сохранить черновик. Не закрывайте страницу до получения результата.",
        ),
      );
    }
  }
  useEffect(() => {
    if (!result) persist(plan, step, attempt);
  }, [plan, step, attempt, result]);
  const count = (v: string) =>
    /^[1-9]\d{0,9}$/.test(v) && BigInt(v) <= 1000000000n ? BigInt(v) : 0n;
  const total = count(plan.quantity),
    offered = count(plan.ipo_quantity),
    price = normalizeAmount(plan.price),
    priceCents = price ? cents(price) : 0n;
  const share =
    total && offered <= total ? Number((offered * 10000n) / total) / 100 : 0;
  const accounts = (org?.accounts || []).filter((a) => a.status === "active");
  const allowed = grants.includes("exchange.issuer.manage");
  function update(field: keyof Plan, value: string) {
    setPlan((p) => ({ ...p, [field]: value }));
    setError("");
  }
  async function validate(at: number) {
    if (
      at === 0 &&
      (!org || !allowed || !accounts.some((a) => a.id === plan.account_id))
    )
      throw new Error(
        ui(
          "Выберите организацию и действующий счёт. Для выпуска нужно разрешение «Управление выпуском акций».",
        ),
      );
    if (at === 1) {
      if (
        !/^[A-Z][A-Z0-9]{1,9}$/.test(plan.ticker) ||
        plan.name.trim().length < 2 ||
        !plan.description.trim()
      )
        throw new Error(
          ui(
            "Укажите название, описание и тикер: 2–10 латинских букв или цифр, первая — буква.",
          ),
        );
      const value = await exchangeApi.get<{
        available: boolean;
      }>("ticker-availability/" + plan.ticker);
      setAvailability({ ticker: plan.ticker, available: value.available });
      if (!value.available)
        throw new Error(ui("Этот тикер уже занят. Выберите другой."));
    }
    if (at === 2 && (!total || !offered || offered > total))
      throw new Error(
        ui(
          "Общее количество — от 1 до 1 000 000 000. На IPO можно предложить от 1 до общего количества акций.",
        ),
      );
    if (at === 3 && !price)
      throw new Error(
        ui(
          "Укажите положительную цену одной акции с точностью до двух знаков.",
        ),
      );
    if (at === 4) {
      const start = new Date(plan.starts_at).getTime(),
        end = new Date(plan.ends_at).getTime();
      if (
        !Number.isFinite(start) ||
        !Number.isFinite(end) ||
        end <= Math.max(start, Date.now())
      )
        throw new Error(
          ui(
            "Выберите начало и будущее окончание IPO. Окончание должно быть позже начала.",
          ),
        );
      if (
        plan.max_per_buyer &&
        (!count(plan.max_per_buyer) || count(plan.max_per_buyer) > offered)
      )
        throw new Error(
          ui("Лимит покупателя — целое число от 1 до количества акций на IPO."),
        );
      if (!plan.terms.trim() || !plan.rights.trim())
        throw new Error(ui("Опишите условия размещения и права акционеров."));
    }
  }
  async function next(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await validate(step);
      setStep((v) => Math.min(6, v + 1));
    } catch (e) {
      setError(
        e instanceof ApiFailure || e instanceof TypeError
          ? localError(e)
          : e instanceof Error
            ? e.message
            : localError(e),
      );
    } finally {
      setBusy(false);
    }
  }
  function planTerms() {
    return uit(
      "{0}\n\nПлан IPO (открывается отдельно после утверждения): {1} акций по {2} ДБ; начало {3}, окончание {4}; лимит покупателя {5}. Всего акций: {6}.",
      plan.terms.trim(),
      plan.ipo_quantity,
      price,
      new Date(plan.starts_at).toISOString(),
      new Date(plan.ends_at).toISOString(),
      plan.max_per_buyer || ui("без дополнительного ограничения"),
      plan.quantity,
    );
  }
  async function submit() {
    if (sending.current || (!consent && !attempt)) return;
    sending.current = true;
    setBusy(true);
    setError("");
    try {
      let current = attempt;
      if (!current) {
        for (let i = 0; i <= 4; i++) await validate(i);
        const body = {
          confirmed: true,
          organization_id: plan.organization_id,
          account_id: plan.account_id,
          ticker: plan.ticker,
          name: plan.name.trim(),
          description: plan.description.trim(),
          terms: planTerms(),
          rights: plan.rights.trim(),
          quantity: Number(total),
        };
        if (body.terms.length > 4000)
          throw new Error(
            ui(
              "Сократите условия размещения: вместе с планом IPO они должны помещаться в 4000 символов.",
            ),
          );
        current = {
          key: crypto.randomUUID(),
          submitKey: crypto.randomUUID(),
          body,
        };
        persist(plan, 6, current);
        setAttempt(current);
      }
      if (!current.stockId) {
        const created = await exchangeApi.write<Stock>(
          "issuers",
          current.body,
          current.key,
        );
        current = { ...current, stockId: created.id };
        setAttempt(current);
        persist(plan, 6, current);
        try {
          localStorage.setItem(
            `horizon.ipo-plan.${data.profile.id}.${created.id}`,
            JSON.stringify({
              quantity: plan.ipo_quantity,
              price,
              starts_at: plan.starts_at,
              ends_at: plan.ends_at,
              max_per_buyer: plan.max_per_buyer,
            }),
          );
        } catch {
          /* The proposed plan is also recorded in issuer terms. */
        }
      }
      const submitted = await exchangeApi.write<Stock>(
        `issuers/${current.stockId}/submit`,
        { confirmed: true },
        current.submitKey,
      );
      setResult(submitted);
      try {
        localStorage.removeItem(key);
      } catch {
        /* The confirmed result is displayed below. */
      }
      await onSubmitted().catch(() => undefined);
    } catch (e) {
      setError(
        e instanceof Error && !("code" in e) && !(e instanceof TypeError)
          ? e.message
          : localError(e),
      );
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  const summary = (
    <div className="flow-summary">
      <div className="stock-identity">
        <span className="stock-monogram">
          {plan.ticker.slice(0, 2) || "HB"}
        </span>
        <div>
          <h3>
            {plan.ticker} · {plan.name}
          </h3>
          <small>{org?.name}</small>
        </div>
      </div>
      <dl className="key-values">
        <div>
          <dt>{ui("Счёт организации")}</dt>
          <dd>
            {accounts.find((a) => a.id === plan.account_id)?.name ||
              ui("Сохранённый счёт")}
          </dd>
        </div>
        <div>
          <dt>{ui("Всего акций")}</dt>
          <dd>{total.toLocaleString(languageLocale())}</dd>
        </div>
        <div>
          <dt>{ui("Для IPO")}</dt>
          <dd>
            {offered.toLocaleString(languageLocale())} · {share}%
          </dd>
        </div>
        <div>
          <dt>{ui("Остаётся компании")}</dt>
          <dd>
            {(total > offered ? total - offered : 0n).toLocaleString(
              languageLocale(),
            )}
          </dd>
        </div>
        <div>
          <dt>{ui("Цена одной акции")}</dt>
          <dd>{price ? money(price) : "—"}</dd>
        </div>
        <div>
          <dt>{ui("Максимум привлечения")}</dt>
          <dd>{money(decimal(priceCents * offered))}</dd>
        </div>
        <div>
          <dt>{ui("Начало → окончание")}</dt>
          <dd>
            {dateTime(plan.starts_at)} → {dateTime(plan.ends_at)}
          </dd>
        </div>
        <div>
          <dt>{ui("Лимит на покупателя")}</dt>
          <dd>{plan.max_per_buyer || ui("Весь объём IPO")}</dd>
        </div>
      </dl>
      <div className="business-preview">
        <b>{ui("Описание компании")}</b>
        <p>{plan.description}</p>
        <b>{ui("Условия размещения")}</b>
        <p>{plan.terms}</p>
        <b>{ui("Права акционеров")}</b>
        <p>{plan.rights}</p>
      </div>
      <p className="info-box">
        {ui(
          "После утверждения тикер, первоначальное количество, счёт и условия выпуска фиксируются. План IPO не открывает продажи: размещение запускается отдельным подтверждением после рассмотрения.",
        )}
      </p>
    </div>
  );
  return (
    <div className="issuer-wizard" ref={panel}>
      <Steps labels={labels()} current={step} />
      <Notice error={error} />
      {storageWarning && (
        <p role="status" className="info-box">
          {storageWarning}
        </p>
      )}
      {result ? (
        <div className="flow-summary">
          <Empty
            icon="check"
            title={ui("Заявка отправлена на рассмотрение")}
            description={ui(
              "Администратор проверит выпуск. После утверждения откройте IPO, предварительно проверив актуальность дат.",
            )}
          />
          <dl className="key-values">
            <div>
              <dt>{ui("Организация")}</dt>
              <dd>{org?.name}</dd>
            </div>
            <div>
              <dt>{ui("Выпуск")}</dt>
              <dd>
                {result.ticker} · {result.name}
              </dd>
            </div>
            <div>
              <dt>{ui("Статус")}</dt>
              <dd>{exchangeStatus(result.status)}</dd>
            </div>
            <div>
              <dt>{ui("Создан")}</dt>
              <dd>{dateTime(result.created_at || "")}</dd>
            </div>
          </dl>
          {onViewIssues && (
            <button
              className="secondary"
              onClick={() => onViewIssues(plan.organization_id)}
            >
              {ui("К выпускам организации")}
            </button>
          )}
        </div>
      ) : (
        <form onSubmit={(e) => void next(e)}>
          <fieldset className="flow-body" disabled={busy || Boolean(attempt)}>
            <legend>{labels()[step]}</legend>
            {step === 0 && (
              <>
                <label>
                  {ui("Организация")}
                  <select
                    value={plan.organization_id}
                    onChange={(e) => {
                      const selected = orgs.find(
                        (o) => o.id === e.target.value,
                      );
                      if (!selected) return;
                      setPlan((p) => ({
                        ...p,
                        organization_id: selected.id,
                        account_id: "",
                        name: selected.name,
                      }));
                      setError("");
                    }}
                  >
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </label>
                {!org ? (
                  <Loading />
                ) : (
                  <>
                    <div className="stock-identity">
                      <span className="organization-mark">
                        <Icon name="organization" />
                      </span>
                      <div>
                        <b>{org.name}</b>
                        <small>
                          {allowed
                            ? ui("Вы можете управлять выпуском акций")
                            : ui("Нет разрешения на управление выпуском акций")}
                        </small>
                      </div>
                    </div>
                    {!allowed && (
                      <p className="error">
                        {ui(
                          "Попросите владельца организации выдать разрешение «Управление выпуском акций». Участия в организации недостаточно.",
                        )}
                      </p>
                    )}
                    <AccountSelect
                      label={ui("Банковский счёт компании")}
                      accounts={accounts}
                      value={plan.account_id}
                      onChange={(id) => update("account_id", id)}
                    />
                  </>
                )}
              </>
            )}
            {step === 1 && (
              <>
                <label>
                  {ui("Название компании")}
                  <input
                    value={plan.name}
                    onChange={(e) => update("name", e.target.value)}
                    minLength={2}
                    maxLength={120}
                    required
                    placeholder={ui("Например, Horizon Construction")}
                  />
                </label>
                <p className="info-box">
                  {ui("Это название выпуска на бирже. Организация-владелец:")}{" "}
                  {org?.name}
                  {ui(". Изменение названия не меняет владельца выпуска.")}
                </p>
                <label>
                  {ui("Тикер")}
                  <input
                    value={plan.ticker}
                    onChange={(e) => {
                      update("ticker", e.target.value.toUpperCase());
                      setAvailability(undefined);
                    }}
                    maxLength={10}
                    pattern="[A-Z][A-Z0-9]{1,9}"
                    required
                    placeholder={ui("Например, HCON")}
                  />
                </label>
                {availability?.ticker === plan.ticker && (
                  <small role="status">
                    {availability.available
                      ? ui("Тикер свободен на момент проверки")
                      : ui("Тикер занят")}
                  </small>
                )}
                <Help term={ui("Что такое тикер?")}>
                  {ui(
                    "Короткое имя акции на бирже: от 2 до 10 латинских букв или цифр. Уникальность проверяется при переходе дальше и окончательно при создании.",
                  )}
                </Help>
                <label>
                  {ui("Описание компании")}
                  <textarea
                    value={plan.description}
                    onChange={(e) => update("description", e.target.value)}
                    maxLength={2000}
                    required
                    rows={3}
                    placeholder={ui(
                      "Чем занимается компания и на что планирует привлечь средства",
                    )}
                  />
                </label>
              </>
            )}
            {step === 2 && (
              <>
                <label>
                  {ui("Общее количество акций")}
                  <input
                    value={plan.quantity}
                    onChange={(e) => update("quantity", e.target.value)}
                    inputMode="numeric"
                    required
                  />
                </label>
                <label>
                  {ui("Количество акций для IPO")}
                  <input
                    value={plan.ipo_quantity}
                    onChange={(e) => update("ipo_quantity", e.target.value)}
                    inputMode="numeric"
                    required
                  />
                </label>
                <div className="distribution-chart">
                  <svg
                    className="distribution-ring"
                    viewBox="0 0 42 42"
                    role="img"
                    aria-label={uit(
                      "Для IPO {0}%, остаётся компании {1}%",
                      share,
                      100 - share,
                    )}
                  >
                    <circle
                      cx="21"
                      cy="21"
                      r="16"
                      fill="none"
                      stroke="var(--surface-raised)"
                      strokeWidth="6"
                    />
                    <circle
                      cx="21"
                      cy="21"
                      r="16"
                      fill="none"
                      stroke="var(--accent)"
                      strokeWidth="6"
                      pathLength="100"
                      strokeDasharray={`${share} ${100 - share}`}
                    />
                  </svg>
                  <div>
                    <p>
                      <b>{share}%</b>
                      {ui(" для продажи ·")}{" "}
                      {offered.toLocaleString(languageLocale())}
                      {ui(" акций")}
                    </p>
                    <p>
                      <b>{100 - share}%</b>
                      {ui(" компании ·")}{" "}
                      {(total > offered ? total - offered : 0n).toLocaleString(
                        languageLocale(),
                      )}{" "}
                      {ui("акций")}
                    </p>
                  </div>
                </div>
                <small>
                  {ui(
                    "IPO — первая продажа части акций компании. Оставшиеся акции находятся на счёте компании.",
                  )}
                </small>
              </>
            )}
            {step === 3 && (
              <>
                <label>
                  {ui("Цена одной акции, ДБ")}
                  <input
                    value={plan.price}
                    onChange={(e) => update("price", e.target.value)}
                    inputMode="decimal"
                    required
                    placeholder={ui("Например, 10,00")}
                  />
                </label>
                <div className="calculation-grid">
                  <div>
                    <small>{ui("Максимум привлечения")}</small>
                    <b>{money(decimal(priceCents * offered))}</b>
                  </div>
                  <div>
                    <small>{ui("Оценка всей компании по цене IPO")}</small>
                    <b>{money(decimal(priceCents * total))}</b>
                  </div>
                </div>
                <p className="muted">
                  {ui(
                    "Оценка компании — цена всех акций. Деньги поступят только за фактически купленные на IPO акции, поэтому оценка не равна привлечённым средствам.",
                  )}
                </p>
                <div className="business-preview">
                  {[1n, 10n, 100n]
                    .filter((n) => n <= offered)
                    .map((n) => (
                      <p key={String(n)}>
                        {ui("Пакет ")}
                        {String(n)}
                        {ui(" акций:")} <b>{money(decimal(priceCents * n))}</b>
                      </p>
                    ))}
                </div>
              </>
            )}
            {step === 4 && (
              <>
                <label>
                  {ui("Дата начала IPO")}
                  <input
                    type="datetime-local"
                    value={plan.starts_at}
                    onChange={(e) => update("starts_at", e.target.value)}
                    required
                  />
                </label>
                <label>
                  {ui("Дата окончания IPO")}
                  <input
                    type="datetime-local"
                    value={plan.ends_at}
                    onChange={(e) => update("ends_at", e.target.value)}
                    required
                  />
                </label>
                <small>
                  {ui("Время вашего устройства:")}{" "}
                  {Intl.DateTimeFormat().resolvedOptions().timeZone}
                  {ui(
                    ". Если рассмотрение затянется, проверьте даты перед открытием IPO.",
                  )}
                </small>
                <label>
                  {ui("Условия размещения")}
                  <textarea
                    value={plan.terms}
                    onChange={(e) => update("terms", e.target.value)}
                    maxLength={3200}
                    rows={3}
                    required
                    placeholder={ui(
                      "На что привлекаются средства, условия и риски",
                    )}
                  />
                </label>
                <label>
                  {ui("Права акционеров")}
                  <textarea
                    value={plan.rights}
                    onChange={(e) => update("rights", e.target.value)}
                    maxLength={2000}
                    rows={3}
                    required
                    placeholder={ui("Какие права получает владелец акции")}
                  />
                </label>
                <Disclosure title={ui("Дополнительные параметры")}>
                  <label>
                    {ui("Максимум акций одному покупателю")}
                    <input
                      value={plan.max_per_buyer}
                      onChange={(e) => update("max_per_buyer", e.target.value)}
                      inputMode="numeric"
                      placeholder={ui("Без дополнительного ограничения")}
                    />
                  </label>
                </Disclosure>
              </>
            )}
            {step >= 5 && summary}
          </fieldset>
          {step === 6 && (
            <>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  disabled={busy || Boolean(attempt)}
                />
                {ui(
                  "Я проверил выпуск и отправляю его на рассмотрение. Продажи сейчас не начнутся.",
                )}
              </label>
              {attempt && (
                <p className="info-box">
                  {ui(
                    "Отправка начата. Повторная проверка использует те же данные и ключи, даже после потери ответа.",
                  )}
                </p>
              )}
              <button
                type="button"
                className="text-button"
                onClick={api.discordLogin}
              >
                {ui("Повторно подтвердить личность через Discord")}
              </button>
            </>
          )}
          <div className="flow-footer">
            <button
              type="button"
              className="secondary"
              disabled={busy || step === 0 || Boolean(attempt)}
              onClick={() => {
                setStep((v) => v - 1);
                setError("");
              }}
            >
              {ui("Назад")}
            </button>
            {step < 6 ? (
              <button disabled={busy || (step === 0 && (!org || !allowed))}>
                {ui("Далее")}
                <Icon name="arrow" size={17} />
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || (!consent && !attempt)}
                onClick={() => void submit()}
              >
                {busy
                  ? ui("Отправляем…")
                  : attempt
                    ? ui("Проверить / повторить отправку")
                    : ui("Отправить на рассмотрение")}
              </button>
            )}
          </div>
          <small>
            {ui(
              "Черновик сохраняется только на этом устройстве и доступен этому профилю. Он не содержит паролей или токенов.",
            )}
          </small>
          {!attempt && (
            <button
              type="button"
              className="text-button danger"
              disabled={busy}
              onClick={() => {
                setPlan(initial(organization));
                setStep(0);
                setError("");
              }}
            >
              {ui("Очистить черновик")}
            </button>
          )}
        </form>
      )}
    </div>
  );
}
