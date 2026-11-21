import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiFailure } from "../api";
import { useBank } from "../bank-context";
import { Avatar, Empty, Icon, PageHeading } from "../components/BankUI";
import { Notice } from "../components/Notice";
import { AccountSelect, Disclosure, Steps } from "../components/Flow";
import { RecipientPicker } from "../components/RecipientPicker";
import { ReceiptDetails } from "../components/Receipt";
import {
  currencyLabel,
  dateTime,
  localError,
  money,
  normalizeAmount,
  useI18n,
} from "../i18n";
import type { Operation, Recipient, TransferQuote } from "../types";
export function Transfer() {
  const { data, refresh, transferPreset, navigate } = useBank();
  const { t, language } = useI18n();
  const [recipient, setRecipient] = useState<Recipient>();
  const [source, setSource] = useState(data.primary_account.id);
  const [mode, setMode] = useState<"external" | "internal">(
    transferPreset?.mode ?? "external",
  );
  const [destination, setDestination] = useState("");
  const [organizationIds, setOrganizationIds] = useState<string[]>([]);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [quote, setQuote] = useState<TransferQuote>();
  const [completed, setCompleted] = useState<Operation>();
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<Recipient[]>([]);
  const [recent, setRecent] = useState<Recipient[]>([]);
  const [contactsError, setContactsError] = useState("");
  const sending = useRef(false);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (quote || completed)
      panel.current?.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [quote?.id, completed?.id]);
  const pendingKey = "horizon.pending." + data.profile.id;
  const internalAccounts = data.accounts.filter(
    (a) =>
      a.status === "active" &&
      a.permissions.includes("accounts.read") &&
      a.permissions.includes("balance.read") &&
      ((a.account_type === "personal" && a.owner_id === data.profile.id) ||
        (a.account_type === "organization" &&
          organizationIds.includes(a.organization_id))),
  );
  const sources = (
    mode === "internal" ? internalAccounts : data.accounts
  ).filter((a) => a.status === "active" && a.permissions.includes("transfer"));
  useEffect(() => {
    if (mode !== "internal") return;
    let active = true;
    api
      .organizations()
      .then((items) => {
        if (active) setOrganizationIds(items.map((org) => org.id));
      })
      .catch((e) => {
        if (active) setError(localError(e, language));
      });
    return () => {
      active = false;
    };
  }, [mode, language]);
  useEffect(() => {
    let active = true;
    Promise.all([api.savedRecipients(), api.recentRecipients()])
      .then(([favorites, history]) => {
        if (active) {
          setSaved(favorites);
          setRecent(history);
        }
      })
      .catch((e) => {
        if (active) setContactsError(localError(e, language));
      });
    const pending = sessionStorage.getItem(pendingKey);
    if (pending) {
      let stored: {
        id: string;
        submitted: boolean;
      };
      try {
        stored = JSON.parse(pending) as typeof stored;
      } catch {
        stored = { id: pending, submitted: true };
      }
      setBusy(true);
      api
        .quote(stored.id)
        .then((value) => {
          if (active) {
            setQuote(value);
            setMode(value.internal ? "internal" : "external");
            setUncertain(stored.submitted);
          }
        })
        .catch((e) => {
          if (active) setError(localError(e, language));
          if (e instanceof ApiFailure && e.status === 404)
            sessionStorage.removeItem(pendingKey);
        })
        .finally(() => {
          if (active) setBusy(false);
        });
    } else if (transferPreset) {
      setMode(transferPreset.mode ?? "external");
      setDestination("");
      setAmount(transferPreset.amount ?? "");
      setReason(transferPreset.reason ?? "");
      setSource(transferPreset.source ?? data.primary_account.id);
      if (transferPreset.recipient)
        api
          .resolveRecipient(transferPreset.recipient)
          .then((value) => {
            if (active) setRecipient(value);
          })
          .catch((e) => {
            if (active) setError(localError(e, language));
          });
    }
    return () => {
      active = false;
    };
  }, [pendingKey, transferPreset, data.primary_account.id, language]);
  async function prepare(event: FormEvent) {
    event.preventDefault();
    setError("");
    const canonical = normalizeAmount(amount);
    if (mode === "external" && !recipient) {
      setError(
        t(
          "Сначала найдите и выберите получателя",
          "Find and select a recipient first",
        ),
      );
      return;
    }
    const target = internalAccounts.find((a) => a.id === destination);
    if (
      mode === "internal" &&
      (!target ||
        destination === source ||
        !sources.some((a) => a.id === source))
    ) {
      setError(
        t(
          "Выберите два разных доступных счёта. Для отправки нужно право перевода.",
          "Choose two different accessible accounts. Sending requires a transfer mandate.",
        ),
      );
      return;
    }
    if (!canonical) {
      setError(
        t(
          "Введите положительную сумму, не более двух знаков после запятой",
          "Enter a positive amount with up to two decimal places",
        ),
      );
      return;
    }
    setBusy(true);
    try {
      const value = await api.prepareTransfer({
        recipient:
          mode === "internal" && target
            ? { kind: "account", value: target.public_number }
            : recipient?.kind === "user" && recipient.id
              ? { kind: "user", value: recipient.id }
              : { kind: "account", value: recipient!.public_number },
        ...(mode === "internal" ? { internal: true } : {}),
        source_account_id: source,
        amount: canonical,
        reason,
      });
      setQuote(value);
      setUncertain(false);
      sessionStorage.setItem(
        pendingKey,
        JSON.stringify({ id: value.id, submitted: false }),
      );
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!quote || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError("");
    sessionStorage.setItem(
      pendingKey,
      JSON.stringify({ id: quote.id, submitted: true }),
    );
    try {
      const result = await api.confirmTransfer(quote);
      setCompleted(result);
      setQuote(undefined);
      setUncertain(false);
      sessionStorage.removeItem(pendingKey);
      await refresh().catch(() => undefined);
    } catch (e) {
      const unknown = !(e instanceof ApiFailure) || e.status >= 500;
      setUncertain(unknown);
      setError(localError(e, language));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  function reset() {
    setQuote(undefined);
    setCompleted(undefined);
    setUncertain(false);
    setError("");
    sessionStorage.removeItem(pendingKey);
  }
  async function removeFavorite(id: string) {
    try {
      await api.unsaveRecipient(id);
      setSaved((value) => value.filter((item) => item.saved_id !== id));
    } catch (e) {
      setContactsError(localError(e, language));
    }
  }
  return (
    <>
      <PageHeading
        title={t("Переводы", "Transfers")}
        subtitle={t(
          "Другому человеку или между вашими счетами — за пару шагов",
          "Send to someone or move money between your accounts in a few steps",
        )}
        action={
          <button className="secondary" onClick={() => navigate("history")}>
            <Icon name="history" />
            {t("История переводов", "Transfer history")}
          </button>
        }
      />
      {!quote && !completed && (
        <div
          className="transfer-mode"
          aria-label={t("Вид перевода", "Transfer type")}
        >
          {(["external", "internal"] as const).map((value) => (
            <button
              key={value}
              disabled={busy}
              aria-pressed={mode === value}
              onClick={() => {
                setMode(value);
                setError("");
              }}
            >
              {value === "external"
                ? t("Другому человеку", "To someone")
                : t("Между счетами", "Between accounts")}
            </button>
          ))}
        </div>
      )}
      <div className="transfer-layout">
        <section className="panel transfer-panel" ref={panel}>
          <Steps
            labels={[
              t("Данные", "Details"),
              t("Проверка", "Review"),
              t("Готово", "Done"),
            ]}
            current={completed ? 2 : quote ? 1 : 0}
          />
          <Notice error={error} />
          {completed ? (
            <>
              <ReceiptDetails operation={completed} />
              <button
                className="wide-button"
                onClick={() => {
                  reset();
                  setRecipient(undefined);
                  setAmount("");
                  setReason("");
                }}
              >
                {t("Новый перевод", "New transfer")}
              </button>
            </>
          ) : quote ? (
            <div className="transfer-confirmation">
              <div className="confirmation-step">
                <Icon name="shield" />
                {t("Шаг 2 из 3 · Подтверждение", "Step 2 of 3 · Confirmation")}
              </div>
              <Avatar name={quote.recipient.display_name} large />
              <h2>{t("Проверьте перевод", "Review your transfer")}</h2>
              <p>
                {quote.recipient.display_name}
                <b className="block-name">
                  {quote.recipient.bank_username
                    ? "@" + quote.recipient.bank_username
                    : quote.recipient.public_number}
                </b>
              </p>
              <strong className="confirmation-amount">
                {money(quote.amount.amount, language)}
              </strong>
              <dl className="key-values">
                <div>
                  <dt>{t("Получатель", "Recipient")}</dt>
                  <dd>{quote.recipient.display_name}</dd>
                </div>
                <div>
                  <dt>{t("Счёт получателя", "Recipient account")}</dt>
                  <dd className="account-review">
                    <span>{quote.recipient.account_name}</span>
                    <small>{quote.recipient.public_number}</small>
                  </dd>
                </div>
                <div>
                  <dt>{t("Со счёта", "From account")}</dt>
                  <dd className="account-review">
                    <span>{quote.source.name}</span>
                    <small>{quote.source.public_number}</small>
                  </dd>
                </div>
                <div>
                  <dt>{t("Комиссия", "Fee")}</dt>
                  <dd>{money(quote.fee.amount, language)}</dd>
                </div>
                <div className="total">
                  <dt>{t("Итого", "Total")}</dt>
                  <dd>{money(quote.total.amount, language)}</dd>
                </div>
                {quote.reason && (
                  <div>
                    <dt>{t("Комментарий", "Comment")}</dt>
                    <dd>{quote.reason}</dd>
                  </div>
                )}
              </dl>
              <p className="field-hint">
                {t("Подтверждение до ", "Confirm before ")}
                {dateTime(quote.expires_at, language)}.{" "}
                {t(
                  "При повторе результат будет проверен без повторного списания.",
                  "Retries check the existing result without a second debit.",
                )}
              </p>
              {uncertain && (
                <div className="info-box">
                  <Icon name="info" />
                  {t(
                    "Результат ещё не подтверждён на этом устройстве. Проверьте его тем же подтверждением; не создавайте второй перевод.",
                    "The outcome is not confirmed on this device. Check it using the same confirmation; do not create another transfer.",
                  )}
                </div>
              )}
              <button
                className="wide-button"
                disabled={busy}
                onClick={() => void confirm()}
              >
                <Icon name={busy ? "clock" : "check"} />
                {busy
                  ? t("Проверяем и выполняем…", "Checking and processing…")
                  : uncertain
                    ? t(
                        "Проверить результат / повторить",
                        "Check outcome / retry",
                      )
                    : t("Подтвердить перевод", "Confirm transfer")}
              </button>
              {!uncertain && (
                <button
                  className="secondary wide-button"
                  disabled={busy}
                  onClick={reset}
                >
                  {t("Отмена", "Cancel")}
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="section-heading">
                <h2>
                  {mode === "internal"
                    ? t("Перевод между счетами", "Between your accounts")
                    : t("Новый перевод", "New transfer")}
                </h2>
                <span className="tag">{t("Комиссия 0%", "0% fee")}</span>
              </div>
              <form
                className="transfer-form"
                onSubmit={(event) => void prepare(event)}
              >
                <AccountSelect
                  label={t("Откуда", "From")}
                  accounts={sources}
                  value={source}
                  onChange={(id) => {
                    setSource(id);
                    if (id === destination) setDestination("");
                  }}
                  disabled={busy}
                />
                {mode === "internal" ? (
                  <>
                    <div className="swap-row">
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={t(
                          "Поменять счета местами",
                          "Swap accounts",
                        )}
                        disabled={
                          busy ||
                          !destination ||
                          !sources.some((a) => a.id === destination) ||
                          !internalAccounts.some((a) => a.id === source)
                        }
                        onClick={() => {
                          setSource(destination);
                          setDestination(source);
                        }}
                      >
                        <Icon name="swap" />
                      </button>
                    </div>
                    <AccountSelect
                      label={t("Куда", "To")}
                      accounts={internalAccounts}
                      exclude={source}
                      value={destination}
                      onChange={setDestination}
                      disabled={busy}
                    />
                    {internalAccounts.length < 2 && (
                      <p className="field-hint">
                        {t(
                          "Нужны два доступных счёта. Откройте ещё один личный счёт или получите доступ к счёту организации.",
                          "You need two accessible accounts. Open another personal account or request access to an organization account.",
                        )}
                      </p>
                    )}
                  </>
                ) : (
                  <RecipientPicker value={recipient} onChange={setRecipient} />
                )}
                <label className="amount-label">
                  {t("Сумма", "Amount")}
                  <div className="amount-input">
                    <input
                      aria-label={t("Сумма перевода", "Transfer amount")}
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      inputMode="decimal"
                      placeholder={language !== "en" ? "0,00" : "0.00"}
                      maxLength={24}
                      required
                    />
                    <span>{currencyLabel(language)}</span>
                  </div>
                </label>
                <label>
                  {t("Комментарий", "Comment")}
                  <span className="optional">
                    {t("Необязательно", "Optional")}
                  </span>
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder={t(
                      "Например, за алмазы",
                      "For example, for the diamonds",
                    )}
                    maxLength={500}
                    rows={2}
                  />
                </label>
                <div className="transfer-fee">
                  <span>{t("Комиссия", "Fee")}</span>
                  <b>{t("Без комиссии", "No fee")}</b>
                </div>
                <Disclosure
                  title={t("Дополнительные параметры", "Additional details")}
                >
                  <p className="muted">
                    {t(
                      "Денежные операции защищены от повторного списания. Номер подтверждения и реквизиты появятся в квитанции.",
                      "Transfers are protected against duplicate debits. Confirmation and account details will appear in your receipt.",
                    )}
                  </p>
                </Disclosure>
                <button className="wide-button" disabled={busy}>
                  {busy
                    ? t("Подготавливаем…", "Preparing…")
                    : t("Продолжить", "Continue")}
                  <Icon name="arrow" />
                </button>
                <small className="secure-note">
                  <Icon name="shield" size={15} />
                  {t(
                    "Деньги спишутся только после вашего подтверждения",
                    "Funds are debited only after you confirm",
                  )}
                </small>
              </form>
            </>
          )}
        </section>
        {!quote &&
          !completed &&
          (mode === "internal" ? (
            <aside className="transfer-aside">
              <section className="panel">
                <h3>
                  {t(
                    "Ваши деньги — там, где нужны",
                    "Your money, where you need it",
                  )}
                </h3>
                <p className="muted">
                  {t(
                    "Переводите между личными и доступными счетами организаций. Баланс в списке — сумма, доступная сейчас, без резервов.",
                    "Move money between personal and accessible organization accounts. Listed balances exclude reserved funds.",
                  )}
                </p>
                <div className="info-box">
                  <Icon name="shield" />
                  <p>
                    {t(
                      "Доступ к просмотру счёта не даёт права списания. Для отправки со счёта организации нужно отдельное финансовое разрешение.",
                      "Viewing an account does not grant permission to debit it. Sending from an organization requires a financial mandate.",
                    )}
                  </p>
                </div>
              </section>
            </aside>
          ) : (
            <aside className="transfer-aside">
              <section className="panel">
                <h3>{t("Недавние получатели", "Recent recipients")}</h3>
                <Notice error={contactsError} />
                {recent.length ? (
                  <div className="recent-people">
                    {recent.map((item) => (
                      <button
                        key={item.account_id}
                        disabled={Boolean(quote) || busy}
                        onClick={() => setRecipient(item)}
                      >
                        <Avatar name={item.display_name} />
                        <span>{item.bank_username || item.display_name}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="muted">
                    {t(
                      "Здесь появятся те, кому вы отправите перевод.",
                      "People you send transfers to will appear here.",
                    )}
                  </p>
                )}
              </section>
              <section className="panel">
                <h3>{t("Избранные", "Saved recipients")}</h3>
                {saved.length ? (
                  <div className="saved-list">
                    {saved.map((item) => (
                      <div key={item.saved_id}>
                        <button
                          disabled={!item.available || Boolean(quote) || busy}
                          onClick={() => setRecipient(item)}
                        >
                          <Avatar
                            name={item.display_name || item.label || "?"}
                          />
                          <span>
                            <b>{item.label || item.display_name}</b>
                            <small>
                              {item.available
                                ? item.bank_username
                                  ? "@" + item.bank_username
                                  : item.public_number
                                : t(
                                    "Получатель недоступен",
                                    "Recipient unavailable",
                                  )}
                            </small>
                          </span>
                        </button>
                        <button
                          className="icon-button"
                          aria-label={t(
                            "Удалить из избранных",
                            "Remove saved recipient",
                          )}
                          onClick={() => void removeFavorite(item.saved_id!)}
                        >
                          <Icon name="close" size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty
                    icon="star"
                    title={t("Близкие — под рукой", "Keep your people close")}
                    description={t(
                      "Сохраните получателя из квитанции, чтобы не искать его снова.",
                      "Save a recipient from a receipt to find them quickly next time.",
                    )}
                  />
                )}
              </section>
              <div className="info-box">
                <Icon name="info" />
                <p>
                  {t(
                    "Перевести можно по банковскому имени, подтверждённому Minecraft-нику, Discord или публичному номеру счёта. UUID не нужен.",
                    "Send by bank username, verified Minecraft name, Discord or public account number. No UUID needed.",
                  )}
                </p>
              </div>
            </aside>
          ))}
      </div>
    </>
  );
}
