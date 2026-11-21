import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { useBank } from "../bank-context";
import {
  AccountCard,
  Empty,
  Icon,
  Loading,
  Modal,
  Operations,
  PageHeading,
} from "../components/BankUI";
import { Notice } from "../components/Notice";
import { localError, money, useI18n } from "../i18n";
import type { AccountBalance, Operation } from "../types";

function AccountDetails({
  initial,
  onClose,
}: {
  initial: AccountBalance;
  onClose: () => void;
}) {
  const { data, refresh, openReceipt, navigate, betweenAccounts } = useBank();
  const { t, language } = useI18n();
  const [account, setAccount] = useState(initial);
  const [name, setName] = useState(initial.name);
  const [items, setItems] = useState<Operation[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all([
      api.balance(initial.id),
      initial.permissions.includes("history.read")
        ? api.activity({ account_id: initial.id, limit: "5" })
        : Promise.resolve({ items: [] as Operation[] }),
    ])
      .then(([fresh, history]) => {
        if (active) {
          setAccount(fresh);
          setItems(history.items);
        }
      })
      .catch((e) => {
        if (active) setError(localError(e, language));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [initial.id, initial.permissions, language]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setAccount(await api.updateAccount(account.id, { name }));
      await refresh();
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function makePrimary() {
    setBusy(true);
    setError("");
    try {
      setAccount(await api.updateAccount(account.id, { is_primary: true }));
      await refresh();
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(account.public_number);
      setCopied(true);
    } catch {
      setError(
        t(
          "Не удалось скопировать. Выделите реквизиты вручную.",
          "Could not copy. Select the account number manually.",
        ),
      );
    }
  }
  return (
    <Modal title={account.name} onClose={onClose}>
      <Notice error={error} />
      <div className="account-detail-balance">
        <span>{t("Доступно для операций", "Available for transactions")}</span>
        <strong>{money(account.available.amount, language)}</strong>
        <span className="tag">
          {account.status === "active"
            ? t("Активен", "Active")
            : account.status === "frozen"
              ? t("Заблокирован", "Blocked")
              : t("Закрыт", "Closed")}
        </span>
      </div>
      {account.status === "active" &&
        account.permissions.includes("transfer") && (
          <div className="button-row">
            <button
              onClick={() => {
                onClose();
                navigate("transfers", account.id);
              }}
            >
              <Icon name="outgoing" />
              {t("Перевести", "Send money")}
            </button>
            <button
              className="secondary"
              onClick={() => {
                onClose();
                betweenAccounts(account.id);
              }}
            >
              <Icon name="swap" />
              {t("Между счетами", "Between accounts")}
            </button>
          </div>
        )}
      <dl className="key-values">
        <div>
          <dt>{t("Баланс", "Balance")}</dt>
          <dd>{money(account.balance.amount, language)}</dd>
        </div>
        <div>
          <dt>{t("Заблокировано", "Reserved")}</dt>
          <dd>{money(account.held.amount, language)}</dd>
        </div>
        <div>
          <dt>{t("Публичные реквизиты", "Public account number")}</dt>
          <dd>
            <code>{account.public_number}</code>
            <button
              className="icon-button"
              aria-label={t("Скопировать реквизиты", "Copy account number")}
              onClick={() => void copy()}
            >
              <Icon name={copied ? "check" : "copy"} size={16} />
            </button>
          </dd>
        </div>
      </dl>
      {account.permissions.includes("account.manage") && (
        <form onSubmit={(event) => void save(event)} className="inline-form">
          <label>
            {t("Название счёта", "Account name")}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
              required
            />
          </label>
          <button className="secondary" disabled={busy}>
            {t("Сохранить", "Save")}
          </button>
        </form>
      )}
      {account.owner_id === data.profile.id &&
        account.account_type === "personal" &&
        !account.is_primary &&
        account.status === "active" && (
          <button
            disabled={busy}
            className="secondary wide-button"
            onClick={() => void makePrimary()}
          >
            {t("Сделать основным счётом", "Make primary account")}
          </button>
        )}
      <details className="technical-details">
        <summary>{t("Дополнительные реквизиты", "Additional details")}</summary>
        <p>
          UUID<code>{account.id}</code>
        </p>
        <p>
          {t("Права доступа", "Permissions")}
          <code>{account.permissions.join(", ")}</code>
        </p>
      </details>
      <div className="section-heading">
        <h3>{t("Последние операции", "Recent activity")}</h3>
        {account.permissions.includes("history.read") && (
          <button
            className="text-button"
            onClick={() => {
              onClose();
              navigate("history", account.id);
            }}
          >
            {t("Вся история", "All activity")}
          </button>
        )}
      </div>
      {!account.permissions.includes("history.read") ? (
        <p className="muted">
          {t(
            "Просмотр истории не разрешён владельцем счёта",
            "The account owner has not granted activity access",
          )}
        </p>
      ) : loading ? (
        <Loading />
      ) : items.length ? (
        <Operations
          items={items}
          onOpen={(id) => {
            onClose();
            openReceipt(id);
          }}
        />
      ) : (
        <Empty
          icon="history"
          title={t("Пока без операций", "No activity yet")}
        />
      )}
    </Modal>
  );
}
export function Accounts() {
  const { data, refresh } = useBank();
  const { t, language } = useI18n();
  const [selected, setSelected] = useState<AccountBalance>();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef(crypto.randomUUID());
  function openCreate() {
    key.current = crypto.randomUUID();
    setName("");
    setError("");
    setCreating(true);
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const account = await api.createAccount(name, key.current);
      await refresh();
      setCreating(false);
      setSelected(account);
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  const personal = data.accounts.filter((a) => a.account_type === "personal");
  const corporate = data.accounts.filter(
    (a) => a.account_type === "organization",
  );
  return (
    <>
      <PageHeading
        title={t("Мои счета", "My accounts")}
        subtitle={t(
          "Один счёт для каждого плана. Основной уже открыт для вас.",
          "An account for every plan. Your primary account is already open.",
        )}
        action={
          <button onClick={openCreate}>
            <Icon name="plus" />
            {t("Открыть счёт", "Open account")}
          </button>
        }
      />
      <section className="account-section">
        <div className="section-heading">
          <h2>{t("Личные счета", "Personal accounts")}</h2>
          <span className="count-badge">{personal.length}</span>
        </div>
        <div className="account-grid">
          {personal.map((a) => (
            <AccountCard
              key={a.id}
              account={a}
              onClick={() => setSelected(a)}
            />
          ))}
        </div>
      </section>
      <section className="account-section">
        <div className="section-heading">
          <h2>{t("Счета организаций", "Organization accounts")}</h2>
          <span className="count-badge">{corporate.length}</span>
        </div>
        {corporate.length ? (
          <div className="account-grid">
            {corporate.map((a) => (
              <AccountCard
                key={a.id}
                account={a}
                onClick={() => setSelected(a)}
              />
            ))}
          </div>
        ) : (
          <div className="panel">
            <Empty
              icon="organization"
              title={t(
                "Ваш бизнес начинается с идеи",
                "Your business starts with an idea",
              )}
              description={t(
                "Счета организаций появятся здесь, когда у вас будет доступ к ним.",
                "Organization accounts appear here when access is granted.",
              )}
            />
          </div>
        )}
      </section>
      <div className="info-box">
        <Icon name="shield" />
        <p>
          {t(
            "Основной счёт открывается автоматически. Смена названия не меняет реквизиты и не влияет на средства.",
            "Your primary account opens automatically. Renaming an account does not change its number or funds.",
          )}
        </p>
      </div>
      {selected && (
        <AccountDetails
          initial={selected}
          onClose={() => setSelected(undefined)}
        />
      )}{" "}
      {creating && (
        <Modal
          title={t("Новый личный счёт", "New personal account")}
          onClose={() => {
            if (!busy) setCreating(false);
          }}
        >
          <Notice error={error} />
          <p className="muted">
            {t(
              "Средства не объединяются: у каждого счёта собственные баланс, реквизиты и история.",
              "Each account has its own balance, number and activity. Funds are never merged.",
            )}
          </p>
          <form onSubmit={(event) => void create(event)}>
            <label>
              {t("Название", "Name")}
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t(
                  "Например, Личный резерв",
                  "For example, Savings",
                )}
                maxLength={80}
                required
                disabled={busy}
              />
            </label>
            <button disabled={busy}>
              {busy
                ? t("Открываем…", "Opening…")
                : t("Открыть дополнительный счёт", "Open additional account")}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
