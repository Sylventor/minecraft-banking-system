import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../i18n";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ApiFailure } from "../api";
import { useBank } from "../bank-context";
import { Empty, Loading, Modal, PageHeading } from "../components/BankUI";
import { Notice } from "../components/Notice";
import { Disclosure } from "../components/Flow";
import { RecipientPicker } from "../components/RecipientPicker";
import { dateTime, localError, money, normalizeAmount } from "../i18n";
import {
  paymentApi,
  type PaymentPage,
  type PaymentRequest,
  type PaymentStatus,
} from "../payment-api";
import type { AccountBalance, Recipient } from "../types";
const labels = (): Record<PaymentStatus, string> => ({
  pending: ui("Ожидает оплаты"),
  paid: ui("Оплачен"),
  declined: ui("Отклонён"),
  cancelled: ui("Отменён"),
  expired: ui("Истёк срок"),
});
function pathId() {
  return /^\/payments\/([0-9a-f-]{36})\/?$/.exec(location.pathname)?.[1] || "";
}
export function Payments({
  organizationId,
  initialRequest,
  initialAction,
  onClose,
  onCompleted,
}: {
  organizationId?: string;
  initialRequest?: PaymentRequest;
  initialAction?: "pay" | "decline";
  onClose?: () => void;
  onCompleted?: () => void;
}) {
  useLocale();
  const { data, refresh, openReceipt } = useBank();
  const [tab, setTab] = useState(organizationId ? "outgoing" : "incoming");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [order, setOrder] = useState("desc");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<PaymentPage>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<PaymentRequest>();
  const [selectedId, setSelectedId] = useState(
    initialRequest?.id || (organizationId ? "" : pathId),
  );
  const [source, setSource] = useState("");
  const [confirm, setConfirm] = useState<"pay" | "decline" | "cancel">();
  const [creating, setCreating] = useState(false);
  const [recipient, setRecipient] = useState<Recipient>();
  const [account, setAccount] = useState("");
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [expires, setExpires] = useState("3600");
  const [external, setExternal] = useState("");
  const createKey = useRef(crypto.randomUUID());
  const payKey = useRef<
    | {
        id: string;
        source: string;
        key: string;
      }
    | undefined
  >(undefined);
  const createAttempt = useRef<
    | {
        payload: string;
        key: string;
      }
    | undefined
  >(undefined);
  const [uncertain, setUncertain] = useState(false);
  const loadSequence = useRef(0);
  const accounts = data.accounts.filter(
    (a) =>
      a.status === "active" &&
      a.permissions.includes("payment_requests.create") &&
      (!organizationId || a.organization_id === organizationId),
  );
  const sources = data.accounts.filter(
    (a) =>
      a.status === "active" &&
      a.account_type === "personal" &&
      a.owner_id === data.profile.id &&
      a.permissions.includes("payment.create"),
  );
  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        offset: String(offset),
        limit: "20",
        order,
      });
      if (status) params.set("status", status);
      if (search) params.set("external_id", search);
      if (organizationId) params.set("organization_id", organizationId);
      const result = await paymentApi.list(tab, params);
      if (sequence === loadSequence.current) setPage(result);
    } catch (e) {
      if (sequence === loadSequence.current) setError(localError(e));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [tab, status, search, offset, order, organizationId]);
  useEffect(() => {
    if (initialRequest) return;
    void load();
  }, [load, initialRequest]);
  useEffect(() => {
    if (organizationId || initialRequest) return;
    const change = () => setSelectedId(pathId());
    window.addEventListener("popstate", change);
    return () => window.removeEventListener("popstate", change);
  }, [organizationId, initialRequest]);
  useEffect(() => {
    if (!selectedId) {
      setSelected(undefined);
      return;
    }
    let active = true;
    setSelected(undefined);
    setConfirm(undefined);
    setUncertain(false);
    paymentApi
      .get(selectedId)
      .then((p) => {
        if (active) {
          setSelected(p);
          if (
            initialRequest &&
            p.status === "pending" &&
            p.payer_id === data.profile.id
          ) {
            setConfirm(initialAction);
            setSource(sources[0]?.id || "");
          } else if (initialRequest) onCompleted?.();
        }
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, [selectedId]);
  function open(p: PaymentRequest) {
    setSelected(p);
    setSelectedId(p.id);
    setConfirm(undefined);
    setUncertain(false);
    if (!organizationId) history.pushState({}, "", "/payments/" + p.id);
  }
  function close() {
    if (busy || uncertain) return;
    if (initialRequest) {
      onClose?.();
      return;
    }
    setSelected(undefined);
    setSelectedId("");
    setConfirm(undefined);
    if (!organizationId) history.pushState({}, "", "/payments");
  }
  async function act() {
    if (!selected || !confirm) return;
    setBusy(true);
    setError("");
    try {
      let result: PaymentRequest;
      if (confirm === "pay") {
        if (
          payKey.current?.id !== selected.id ||
          payKey.current?.source !== source
        )
          payKey.current = {
            id: selected.id,
            source,
            key: crypto.randomUUID(),
          };
        setUncertain(true);
        result = await paymentApi.pay(selected.id, source, payKey.current.key);
      } else result = await paymentApi.action(selected.id, confirm);
      setUncertain(false);
      setSelected(result);
      setConfirm(undefined);
      await load();
      await refresh();
      onCompleted?.();
    } catch (e) {
      if (e instanceof ApiFailure && e.status >= 400 && e.status < 500)
        setUncertain(false);
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  async function reconcile() {
    if (!selected) return;
    setBusy(true);
    try {
      const p = await paymentApi.get(selected.id);
      setSelected(p);
      if (p.status !== "pending") {
        setUncertain(false);
        setConfirm(undefined);
        await load();
        await refresh();
        onCompleted?.();
      }
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    if (!recipient?.id) return;
    const normalized = normalizeAmount(amount);
    if (!normalized || /^0\.00$/.test(normalized)) {
      setError(ui("Введите положительную сумму с точностью до двух знаков"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body = {
        payer_id: recipient.id,
        recipient_account_id: account,
        amount: normalized,
        description,
        expires_in: Number(expires),
        ...(external ? { external_id: external } : {}),
      };
      const payload = JSON.stringify(body);
      if (createAttempt.current?.payload !== payload)
        createAttempt.current = {
          payload,
          key: (createKey.current = crypto.randomUUID()),
        };
      const result = await paymentApi.create(body, createAttempt.current.key);
      setCreating(false);
      setTab("outgoing");
      setOffset(0);
      await load();
      open(result);
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  const payer = selected?.payer_id === data.profile.id;
  const pending =
    selected?.status === "pending" &&
    new Date(selected.expires_at).getTime() > Date.now();
  const canCancel =
    selected &&
    data.accounts.some(
      (a) =>
        a.id === selected.recipient_account_id &&
        a.permissions.includes("payment_requests.cancel"),
    );
  return (
    <>
      {initialRequest && !selected && (
        <Modal title={ui("Запрос на оплату")} onClose={() => onClose?.()}>
          <Notice error={error} />
          {!error && <Loading />}
        </Modal>
      )}
      {!initialRequest && (
        <>
          <PageHeading
            title={organizationId ? ui("Запросы на оплату") : ui("Платежи")}
            subtitle={ui(
              "Запрос — предложение оплатить. Деньги списываются только после вашего подтверждения.",
            )}
            action={
              accounts.length > 0 ? (
                <button
                  onClick={() => {
                    createKey.current = crypto.randomUUID();
                    setAccount(accounts[0].id);
                    setRecipient(undefined);
                    setAmount("");
                    setDescription("");
                    setExternal("");
                    setError("");
                    setCreating(true);
                  }}
                >
                  {ui("Выставить запрос")}
                </button>
              ) : undefined
            }
          />
          <Notice error={error} />
          <section className="panel payment-panel">
            <div className="payment-tabs">
              {(organizationId
                ? ["outgoing", "history"]
                : ["incoming", "outgoing", "history"]
              ).map((t) => (
                <button
                  key={t}
                  className={tab === t ? "" : "secondary"}
                  onClick={() => {
                    setTab(t);
                    setOffset(0);
                  }}
                >
                  {
                    {
                      incoming: ui("Входящие"),
                      outgoing: ui("Исходящие"),
                      history: ui("История"),
                    }[t]
                  }
                </button>
              ))}
            </div>
            <div className="payment-filters">
              <label>
                {ui("Поиск по заказу")}
                <input
                  value={search}
                  maxLength={120}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setOffset(0);
                  }}
                  placeholder="ORDER-123"
                />
              </label>
              <label>
                {ui("Статус")}
                <select
                  value={status}
                  onChange={(e) => {
                    setStatus(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">{ui("Все статусы")}</option>
                  {Object.entries(labels()).map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {ui("Дата")}
                <select
                  value={order}
                  onChange={(e) => {
                    setOrder(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="desc">{ui("Сначала новые")}</option>
                  <option value="asc">{ui("Сначала старые")}</option>
                </select>
              </label>
              <button className="secondary" onClick={() => void load()}>
                {ui("Обновить")}
              </button>
            </div>
            {loading ? (
              <Loading />
            ) : !page?.items.length ? (
              <Empty
                icon="transfer"
                title={ui("Запросов пока нет")}
                description={ui(
                  "Здесь появятся счета на оплату и их подтверждённые результаты.",
                )}
              />
            ) : (
              <div className="payment-list">
                {page.items.map((p) => (
                  <button
                    className="payment-row secondary"
                    key={p.id}
                    onClick={() => open(p)}
                  >
                    <span>
                      <b>
                        {p.merchant_name} → {p.payer_name}
                      </b>
                      <span>{p.description}</span>
                      <small>
                        {p.external_id || ui("Без номера заказа")}
                        {ui(" · Создан")} {dateTime(p.created_at)}
                        {ui(" · До ")}
                        {dateTime(p.expires_at)}
                      </small>
                    </span>
                    <span>
                      <strong>{money(p.amount)}</strong>
                      <span className="tag">{labels()[p.status]}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
            <div className="payment-pagination">
              <button
                className="secondary"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                {ui("Назад")}
              </button>
              <button
                className="secondary"
                disabled={page?.next_offset == null || loading}
                onClick={() => setOffset(page?.next_offset ?? 0)}
              >
                {ui("Далее")}
              </button>
            </div>
          </section>
        </>
      )}
      {selected && !confirm && (
        <Modal title={ui("Запрос на оплату")} onClose={close}>
          <Notice error={error} />
          <h2>{money(selected.amount)}</h2>
          <p>
            {selected.merchant_name} → {selected.payer_name}
          </p>
          <p>{selected.description}</p>
          <dl className="payment-details">
            <dt>{ui("Получатель")}</dt>
            <dd>
              {selected.recipient_account_name} ·{" "}
              {selected.recipient_public_number}
            </dd>
            <dt>{ui("Статус")}</dt>
            <dd>{labels()[selected.status]}</dd>
            <dt>{ui("Создан")}</dt>
            <dd>{dateTime(selected.created_at)}</dd>
            <dt>{ui("Срок действия")}</dt>
            <dd>{dateTime(selected.expires_at)}</dd>
            <dt>{ui("Заказ")}</dt>
            <dd>{selected.external_id || "—"}</dd>
          </dl>
          {pending && payer && (
            <>
              <label>
                {ui("Счёт списания")}
                <select
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                >
                  <option value="">{ui("Выберите свой счёт")}</option>
                  {sources.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} · {money(a.available.amount)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={!source || busy}
                onClick={() => setConfirm("pay")}
              >
                {ui("Оплатить")}
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => setConfirm("decline")}
              >
                {ui("Отклонить")}
              </button>
            </>
          )}
          {pending && canCancel && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirm("cancel")}
            >
              {ui("Отменить запрос")}
            </button>
          )}
          {selected.status === "paid" && selected.transaction_id && (
            <button onClick={() => openReceipt(selected.transaction_id!)}>
              {ui("Квитанция")}
            </button>
          )}
        </Modal>
      )}
      {selected && confirm && (
        <Modal
          title={
            confirm === "pay"
              ? ui("Подтвердите оплату")
              : confirm === "decline"
                ? ui("Отклонить запрос?")
                : ui("Отменить запрос?")
          }
          onClose={() => {
            if (!busy && !uncertain) {
              setConfirm(undefined);
              onClose?.();
            }
          }}
        >
          <Notice error={error} />
          <h2>{money(selected.amount)}</h2>
          <p>
            {ui("Получатель: ")}
            {selected.merchant_name} · {selected.recipient_public_number}
          </p>
          <p>{selected.description}</p>
          {confirm === "pay" && (
            <>
              {initialRequest && (
                <label>
                  {ui("Счёт списания")}
                  <select
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                    disabled={busy || uncertain}
                  >
                    <option value="">{ui("Выберите счёт")}</option>
                    {sources.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} · {money(a.available.amount)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <p>
                {ui("Списание со счёта: ")}
                {sources.find((a) => a.id === source)?.name}
                {ui(". Комиссия: 0,00 ДБ.")}
              </p>
            </>
          )}
          {uncertain && (
            <p role="status">
              {ui(
                "Результат ещё не получен. Проверьте статус или повторите ту же попытку — ключ оплаты сохранён.",
              )}
            </p>
          )}
          <button
            disabled={busy || (confirm === "pay" && !source)}
            onClick={() => void act()}
          >
            {busy
              ? ui("Проверяем…")
              : confirm === "pay"
                ? uit("Оплатить {0}", money(selected.amount))
                : ui("Подтвердить")}
          </button>
          {uncertain && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void reconcile()}
            >
              {ui("Проверить статус")}
            </button>
          )}
        </Modal>
      )}
      {creating && (
        <Modal
          title={ui("Новый запрос на оплату")}
          onClose={() => {
            if (!busy) setCreating(false);
          }}
        >
          <Notice error={error} />
          <form onSubmit={(e) => void create(e)}>
            <RecipientPicker
              label={ui("Плательщик")}
              value={recipient}
              onChange={setRecipient}
            />
            <label>
              {ui("Счёт получателя")}
              <select
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                required
              >
                {accounts.map((a: AccountBalance) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.public_number}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {ui("Сумма, ДБ")}
              <input
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="decimal"
                required
              />
            </label>
            <label>
              {ui("Назначение")}
              <input
                value={description}
                maxLength={500}
                onChange={(e) => setDescription(e.target.value)}
                required
              />
            </label>
            <label>
              {ui("Срок")}
              <select
                value={expires}
                onChange={(e) => setExpires(e.target.value)}
              >
                <option value="3600">{ui("1 час")}</option>
                <option value="86400">{ui("1 день")}</option>
                <option value="604800">{ui("7 дней")}</option>
              </select>
            </label>
            <Disclosure title={ui("Дополнительные параметры заказа")}>
              <label>
                {ui("Номер заказа (необязательно)")}
                <input
                  value={external}
                  maxLength={120}
                  onChange={(e) => setExternal(e.target.value)}
                />
              </label>
            </Disclosure>
            <button disabled={busy || !recipient?.id || !account}>
              {busy ? ui("Создаём…") : ui("Выставить запрос")}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
