import { localize as ui, useI18n as useLocale } from "../i18n";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useBank } from "../bank-context";
import {
  paymentApi,
  type PaymentPage,
  type PaymentRequest,
} from "../payment-api";
import { localizeSystem } from "../i18n";
import { Loading } from "./BankUI";
const Payments = lazy(() =>
  import("../pages/Payments").then((m) => ({ default: m.Payments })),
);
import { dateTime, money } from "../i18n";
export function PendingPayments() {
  useLocale();
  const { navigate } = useBank();
  const [page, setPage] = useState<PaymentPage>();
  const [action, setAction] = useState<{
    request: PaymentRequest;
    kind: "pay" | "decline";
  }>();
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const result = await paymentApi.list(
        "incoming",
        new URLSearchParams({
          status: "pending",
          order: "desc",
          limit: "5",
          offset: "0",
        }),
      );
      if (current === sequence.current) setPage(result);
    } catch {
      /* The main banking dashboard remains available during a request API outage. */
    }
  }, []);
  useEffect(() => {
    void load();
    const tick = setInterval(() => {
      if (document.visibilityState !== "hidden") void load();
    }, 10000);
    const focus = () => {
      void load();
    };
    window.addEventListener("focus", focus);
    return () => {
      clearInterval(tick);
      window.removeEventListener("focus", focus);
    };
  }, [load]);
  const items = (page?.items || []).filter(
    (p) => p.status === "pending" && Date.parse(p.expires_at) > Date.now(),
  );
  if (!items.length && !action && !notice) return null;
  return (
    <>
      {notice && (
        <p role="status" className="info-box">
          {localizeSystem(notice)}
        </p>
      )}
      {items.length > 0 && (
        <section className="pending-payments panel">
          <div className="section-heading">
            <div>
              <small>{ui("ВХОДЯЩИЕ ЗАПРОСЫ")}</small>
              <h2>{ui("Ожидают оплаты")}</h2>
            </div>
            <span className="tag">
              {ui("Всего: ")}
              {page?.total ?? items.length}
            </span>
          </div>
          <div className="pending-payment-grid">
            {items.slice(0, 5).map((p) => (
              <article key={p.id}>
                <div>
                  <strong>{p.merchant_name || p.creator_name}</strong>
                  <b>{money(p.amount)}</b>
                </div>
                <p>{p.description}</p>
                <small>
                  {ui("Создан: ")}
                  {dateTime(p.created_at)}
                </small>
                <small>
                  {ui("Оплатить до: ")}
                  {dateTime(p.expires_at)}
                </small>
                <span className="tag">{ui("Ожидает оплаты")}</span>
                <div className="pending-payment-actions">
                  <button
                    onClick={() => {
                      setNotice("");
                      setAction({ request: p, kind: "pay" });
                    }}
                  >
                    {ui("Оплатить")}
                  </button>
                  <button
                    className="secondary"
                    onClick={() => {
                      setNotice("");
                      setAction({ request: p, kind: "decline" });
                    }}
                  >
                    {ui("Отклонить")}
                  </button>
                </div>
              </article>
            ))}
          </div>
          {((page?.next_offset !== null && page?.next_offset !== undefined) ||
            (page?.total || 0) > 5) && (
            <button
              className="text-button"
              onClick={() => navigate("payments")}
            >
              {ui("Показать все")}
            </button>
          )}
        </section>
      )}
      {action && (
        <Suspense fallback={<Loading />}>
          <Payments
            key={action.request.id + action.kind}
            initialRequest={action.request}
            initialAction={action.kind}
            onClose={() => setAction(undefined)}
            onCompleted={() => {
              setNotice(ui("Статус запроса обновлён"));
              setPage(
                (value) =>
                  value && {
                    ...value,
                    items: value.items.filter(
                      (p) => p.id !== action.request.id,
                    ),
                    total: Math.max(0, (value.total || value.items.length) - 1),
                  },
              );
              setAction(undefined);
              void load();
            }}
          />
        </Suspense>
      )}
    </>
  );
}
