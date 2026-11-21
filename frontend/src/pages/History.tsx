import { useEffect, useState } from "react";
import { api } from "../api";
import { useBank } from "../bank-context";
import {
  Empty,
  Icon,
  Loading,
  Operations,
  PageHeading,
} from "../components/BankUI";
import { Notice } from "../components/Notice";
import { Disclosure } from "../components/Flow";
import { localError, useI18n } from "../i18n";
import type { ActivityFilters, Operation } from "../types";

export function History() {
  const { data, openReceipt, historyAccount } = useBank();
  const { t, language } = useI18n();
  const [items, setItems] = useState<Operation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [filters, setFilters] = useState<ActivityFilters>({
    account_id: historyAccount,
  });
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const timer = setTimeout(
      () => setFilters((value) => ({ ...value, q: query })),
      350,
    );
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    let active = true;
    setBusy(true);
    setError("");
    api
      .activity(filters)
      .then((page) => {
        if (active) {
          setItems(page.items);
          setCursor(page.next_cursor);
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
  }, [filters, language]);
  async function more() {
    if (!cursor) return;
    setBusy(true);
    try {
      const page = await api.activity({ ...filters, cursor });
      setItems((previous) => [...previous, ...page.items]);
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
        title={t("История операций", "Transaction history")}
        subtitle={t(
          "Каждое движение дебрисов — под вашим контролем",
          "Every movement of your debrys, under your control",
        )}
      />
      <section className="panel">
        <div className="filters">
          <label className="search-field">
            <Icon name="search" />
            <input
              aria-label={t("Поиск в истории", "Search activity")}
              placeholder={t(
                "Комментарий или номер операции",
                "Comment or operation ID",
              )}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <select
            aria-label={t("Фильтр по счёту", "Filter by account")}
            value={filters.account_id ?? ""}
            onChange={(e) =>
              setFilters((value) => ({ ...value, account_id: e.target.value }))
            }
          >
            <option value="">{t("Все мои счета", "All my accounts")}</option>
            {data.accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <select
            aria-label={t("Тип операции", "Operation type")}
            value={filters.operation_type ?? ""}
            onChange={(e) =>
              setFilters((value) => ({
                ...value,
                operation_type: e.target.value,
              }))
            }
          >
            <option value="">{t("Все операции", "All operations")}</option>
            <option value="transfer">{t("Переводы", "Transfers")}</option>
            <option value="payment">{t("Оплаты", "Payments")}</option>
            <option value="refund">{t("Возвраты", "Refunds")}</option>
            <option value="issue">
              {t("Поступления от банка", "Bank allocations")}
            </option>
            <option value="hold">{t("Блокировки", "Reservations")}</option>
            <option value="capture_hold">
              {t("Списание резерва", "Capture reservation")}
            </option>
            <option value="release_hold">
              {t("Снятие блокировки", "Release reservation")}
            </option>
          </select>
          <Disclosure
            title={t("Период и сортировка", "Date range and sorting")}
          >
            <div className="business-filters">
              <select
                aria-label={t("Порядок операций", "Sort activity")}
                value={filters.order ?? "desc"}
                onChange={(e) =>
                  setFilters((value) => ({
                    ...value,
                    order: e.target.value as "asc" | "desc",
                  }))
                }
              >
                <option value="desc">
                  {t("Сначала новые", "Newest first")}
                </option>
                <option value="asc">
                  {t("Сначала старые", "Oldest first")}
                </option>
              </select>
              <label>
                {t("С даты", "From")}
                <input
                  type="date"
                  onChange={(e) =>
                    setFilters((value) => ({
                      ...value,
                      from_date: e.target.value
                        ? `${e.target.value}T00:00:00Z`
                        : "",
                    }))
                  }
                />
              </label>
              <label>
                {t("До даты", "Before")}
                <input
                  type="date"
                  onChange={(e) =>
                    setFilters((value) => ({
                      ...value,
                      to_date: e.target.value
                        ? `${e.target.value}T00:00:00Z`
                        : "",
                    }))
                  }
                />
              </label>
            </div>
          </Disclosure>
        </div>
        <Notice error={error} />
        {busy && items.length === 0 ? (
          <Loading />
        ) : items.length ? (
          <Operations items={items} onOpen={openReceipt} />
        ) : (
          !error && (
            <Empty
              icon="history"
              title={t("Операций пока нет", "No activity yet")}
              description={t(
                "Попробуйте изменить фильтры или совершите первый перевод.",
                "Try different filters or make your first transfer.",
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
    </>
  );
}
