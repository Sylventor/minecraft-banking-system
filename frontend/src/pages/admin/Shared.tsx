import { localize as ui, useI18n as useLocale } from "../../i18n";
import { useEffect, useState, type ReactNode } from "react";
import {
  adminApi,
  type AdminPage,
  type AdminUser,
  type AdminAccount,
} from "../../admin-api";
import { Loading, Modal } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { localError } from "../../i18n";
export function useAdminPage<T>(path: string | null) {
  const [data, setData] = useState<AdminPage<T>>(),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    if (path === null) return;
    let active = true;
    setData(undefined);
    setError("");
    adminApi
      .get<AdminPage<T>>(path)
      .then((x) => {
        if (active) setData(x);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  return { data, error, reload: () => setRevision((x) => x + 1) };
}
export function PageState({
  loading,
  error,
  empty,
}: {
  loading: boolean;
  error: string;
  empty: boolean;
}) {
  useLocale();
  return (
    <>
      <Notice error={error} />
      {loading && !error && <Loading />}
      {empty && (
        <p className="empty-state">
          {ui("По вашему запросу ничего не найдено.")}
        </p>
      )}
    </>
  );
}
export function ActionDialog({
  title,
  children,
  onClose,
  onConfirm,
  reasonRequired = true,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
  reasonRequired?: boolean;
}) {
  useLocale();
  const [reason, setReason] = useState(""),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function confirm() {
    setBusy(true);
    setError("");
    try {
      await onConfirm(reason);
      onClose();
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {children}
      <Notice error={error} />
      {reasonRequired && (
        <label>
          {ui("Причина действия")}
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
            required
          />
        </label>
      )}
      <label className="checkbox">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        {ui("Подтверждаю выбранные данные и действие")}
      </label>
      <button
        disabled={busy || !confirmed || (reasonRequired && !reason.trim())}
        onClick={() => void confirm()}
      >
        {busy ? ui("Выполняем…") : ui("Подтвердить")}
      </button>
    </Modal>
  );
}
export function UserPicker({
  onPick,
  label = ui("Найти пользователя"),
}: {
  onPick: (user: AdminUser) => void;
  label?: string;
}) {
  useLocale();
  const [q, setQ] = useState(""),
    [items, setItems] = useState<AdminUser[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    if (q.trim().length < 2) {
      setItems([]);
      return;
    }
    const timer = setTimeout(() => {
      adminApi
        .users(q)
        .then((x) => {
          if (active) {
            setItems(x.items.slice(0, 8));
            setError("");
          }
        })
        .catch((e) => {
          if (active) setError(localError(e));
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [q]);
  return (
    <div className="admin-picker">
      <label>
        {label}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={ui("Банковский ник, Discord ID или Minecraft")}
        />
      </label>
      <Notice error={error} />
      <div className="admin-picker-results">
        {items.map((user) => (
          <button
            className="secondary"
            key={user.id}
            onClick={() => {
              onPick(user);
              setQ("");
              setItems([]);
            }}
          >
            {user.display_name} <small>@{user.bank_username}</small>
          </button>
        ))}
      </div>
    </div>
  );
}
export function AccountPicker({
  onPick,
}: {
  onPick: (account: AdminAccount) => void;
}) {
  useLocale();
  const [q, setQ] = useState(""),
    [items, setItems] = useState<AdminAccount[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    if (q.trim().length < 2) {
      setItems([]);
      return;
    }
    const timer = setTimeout(() => {
      adminApi
        .accounts(q)
        .then((x) => {
          if (active) {
            setItems(x.items.slice(0, 8));
            setError("");
          }
        })
        .catch((e) => {
          if (active) setError(localError(e));
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [q]);
  return (
    <div className="admin-picker">
      <label>
        {ui("Счёт получателя")}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={ui("Публичный номер HB- или название счёта")}
        />
      </label>
      <Notice error={error} />
      {items.map((a) => (
        <button
          className="secondary"
          key={a.id}
          onClick={() => {
            onPick(a);
            setQ("");
            setItems([]);
          }}
        >
          {a.name} · {a.owner}
          <small>{a.public_number}</small>
        </button>
      ))}
    </div>
  );
}
export function NextPage({
  next,
  onNext,
  onFirst,
}: {
  next: string | number | null | undefined;
  onNext: () => void;
  onFirst: () => void;
}) {
  useLocale();
  return (
    <div className="admin-pagination">
      <button className="secondary" onClick={onFirst}>
        {ui("В начало")}
      </button>
      <button className="secondary" disabled={next == null} onClick={onNext}>
        {ui("Следующая страница")}
      </button>
    </div>
  );
}
