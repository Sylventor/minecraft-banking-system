import { localize as ui } from "../i18n";
import { useEffect, useRef, type ReactNode } from "react";
import {
  currencyLabel,
  dateTime,
  money,
  operationName,
  operationStatus,
  useI18n,
} from "../i18n";
import type { AccountBalance, Operation } from "../types";
import { bankLogoUrl, projectName } from "../branding";
const paths = {
  home: "m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z",
  transfer: "M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4",
  wallet:
    "M3 6a2 2 0 0 1 2-2h13v4H5a2 2 0 0 1 0-4M3 6v13a1 1 0 0 0 1 1h17V8H5m12 5h4v4h-4Z",
  history: "M3 5v5h5M3 10a9 9 0 1 1 1 8m8-12v6l4 2",
  organization:
    "M4 21V6l8-3 8 3v15M2 21h20M8 8h1m6 0h1M8 12h1m6 0h1M10 21v-5h4v5",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 8-3 9h18c0-1-3-2-3-9M10 21h4",
  headset:
    "M3 14v-3a9 9 0 0 1 18 0v3M3 12h3v8H4a1 1 0 0 1-1-1Zm18 0h-3v8h2a1 1 0 0 0 1-1Zm0 7a3 3 0 0 1-3 3h-3",
  newspaper:
    "M4 6H2v14a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V2H6a2 2 0 0 0-2 2v16m4-14h10M8 10h10M8 14h4v4H8Zm8 0h2m-2 4h2",
  settings:
    "m9 3-1 3-3 1-2 3 2 3 1 4 4 1 2 3 3-1 2-3 3-1 1-4-2-3-1-3-4-1Zm3 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8",
  code: "m8 5-7 7 7 7m8-14 7 7-7 7m-3-16-2 18",
  shield: "m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6ZM8 12l3 3 5-6",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  plus: "M12 4v16M4 12h16",
  search: "m20 20-5-5M3 9a6 6 0 1 0 12 0A6 6 0 0 0 3 9",
  chevron: "m9 5 7 7-7 7",
  close: "m6 6 12 12M6 18 18 6",
  logout: "M10 3H4v18h6m4-16 7 7-7 7m-5-7h12",
  check: "m5 12 4 4L19 6",
  clock: "M12 7v5l3 2M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0",
  star: "m12 2 3 6 7 1-5 5 1 8-6-4-6 4 1-8-5-5 7-1Z",
  copy: "M8 8h13v13H8ZM3 16V3h13",
  refresh:
    "M20 4v6h-6M4 20v-6h6M4 8a8 8 0 0 1 13-4l3 6M4 14l3 6a8 8 0 0 0 13-4",
  download: "M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4",
  menu: "M3 6h18M3 12h18M3 18h18",
  info: "M12 10v7m0-11v1M2 12a10 10 0 1 0 20 0 10 10 0 0 0-20 0",
  incoming: "m18 6-12 12m0-8v8h8",
  outgoing: "M6 18 18 6m-8 0h8v8",
  user: "M8 7a4 4 0 1 0 8 0 4 4 0 0 0-8 0M3 22v-3a9 9 0 0 1 18 0v3",
  chart: "M4 3v17h17M7 14l4-4 4 2 5-7",
  sun: "M12 3V1m0 22v-2M3 12H1m22 0h-2M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2M7 12a5 5 0 1 0 10 0 5 5 0 0 0-10 0",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm7 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
  swap: "M7 3v17m-4-4 4 4 4-4M17 21V4m-4 4 4-4 4 4",
};
export type IconName = keyof typeof paths;
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
export function NewsShortcut({ russian = false }: { russian?: boolean }) {
  const { t } = useI18n();
  const label = russian ? ui("Новости") : t("Новости", "News");
  const active = /^\/news(?:\/|$)/.test(location.pathname);
  return (
    <a
      className="icon-button news-shortcut"
      href="/news"
      title={label}
      aria-label={label}
      aria-current={active ? "page" : undefined}
    >
      <Icon name="newspaper" />
    </a>
  );
}
export function Avatar({
  name,
  large = false,
}: {
  name: string;
  large?: boolean;
}) {
  return (
    <span className={`avatar${large ? " large" : ""}`} aria-hidden="true">
      {name
        .split(/\s+/)
        .map((part) => part[0])
        .slice(0, 2)
        .join("")
        .toUpperCase()}
    </span>
  );
}
export function Brand() {
  return (
    <span className="brand">
      {bankLogoUrl && (
        <img
          className="brand-logo"
          src={bankLogoUrl}
          alt=""
          aria-hidden="true"
        />
      )}
      <span>{projectName}</span>
    </span>
  );
}
export function PageHeading({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
export function Loading({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="skeleton-stack" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>{label ?? t("Загружаем данные банка…", "Loading bank data…")}</span>
    </div>
  );
}
export function Empty({
  icon = "wallet",
  title,
  description,
  action,
}: {
  icon?: IconName;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={28} />
      </span>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
export function AccountCard({
  account,
  onClick,
  compact = false,
  concealed = false,
}: {
  account: AccountBalance;
  onClick?: () => void;
  compact?: boolean;
  concealed?: boolean;
}) {
  const { language, t } = useI18n();
  return (
    <button
      className={`account-card${account.is_primary ? " primary" : ""}${compact ? " compact" : ""}`}
      onClick={onClick}
    >
      <span className="account-top">
        <span className="account-icon">
          <Icon name="wallet" />
        </span>
        <span className="currency-chip">{currencyLabel(language)}</span>
      </span>
      <span className="account-label">
        {account.name}
        <span>
          {account.is_primary
            ? t("Основной", "Primary")
            : account.organization_id
              ? t("Организация", "Organization")
              : t("Личный", "Personal")}
        </span>
      </span>
      <strong>
        {concealed
          ? "•••• " + currencyLabel(language)
          : money(account.available.amount, language)}
      </strong>
      <span className="account-bottom">
        <span>
          {account.public_number.slice(0, 7)} ••••{" "}
          {account.public_number.slice(-4)}
        </span>
        <Icon name="chevron" size={16} />
      </span>
    </button>
  );
}
export function Operations({
  items,
  onOpen,
  concealed = false,
}: {
  items: Operation[];
  onOpen: (id: string) => void;
  concealed?: boolean;
}) {
  const { language, t } = useI18n();
  return (
    <div className="operation-list">
      {items.map((item) => (
        <button
          key={item.id}
          className="operation-row"
          onClick={() => onOpen(item.id)}
        >
          <span className={`operation-icon ${item.direction ?? "outgoing"}`}>
            <Icon
              name={
                item.direction === "incoming"
                  ? "incoming"
                  : item.direction === "internal"
                    ? "transfer"
                    : "outgoing"
              }
            />
          </span>
          <span className="operation-person">
            <b>
              {item.counterparty ??
                item.recipient ??
                operationName(item.type, t)}
            </b>
            <small>
              {operationName(item.type, t)}
              {item.reason ? ` · ${item.reason}` : ""}
            </small>
          </span>
          <span className="operation-date">
            {dateTime(item.created_at, language)}
          </span>
          <span
            className={`operation-amount ${item.direction === "incoming" ? "positive" : ""}`}
          >
            {!concealed &&
              (item.direction === "incoming"
                ? "+"
                : item.direction === "internal"
                  ? ""
                  : "−")}
            {concealed
              ? "•••• " + currencyLabel(language)
              : money(item.amount.amount, language)}
            <small>
              {item.status === "completed"
                ? t("Выполнено", "Completed")
                : t("Статус: ", "Status: ") + operationStatus(item.status, t)}
            </small>
          </span>
          <Icon name="chevron" size={16} />
        </button>
      ))}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  closeLabel,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  closeLabel?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { t } = useI18n();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current
      ?.querySelector<HTMLElement>(
        "button, input, select, textarea, [tabindex]",
      )
      ?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab") {
        const elements = ref.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex='0']",
        );
        if (!elements?.length) return;
        const first = elements[0],
          last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = bodyOverflow;
      document.removeEventListener("keydown", keydown);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
      >
        <header>
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label={closeLabel ?? t("Закрыть", "Close")}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
