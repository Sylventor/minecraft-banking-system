import { localize as ui, useI18n as useLocale } from "../i18n";
import type { KeyboardEvent, ReactNode } from "react";
import { Icon } from "./BankUI";
import { useI18n } from "../i18n";
import type { AccountBalance } from "../types";
import { money } from "../i18n";
export function tabKeys(event: KeyboardEvent<HTMLElement>) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [
    ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]:not(:disabled)',
    ),
  ];
  const index = tabs.indexOf(event.target as HTMLButtonElement);
  if (index < 0 || !tabs.length) return;
  event.preventDefault();
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) %
          tabs.length;
  tabs[next].focus();
  tabs[next].click();
}
export function Steps({
  labels,
  current,
}: {
  labels: string[];
  current: number;
}) {
  useLocale();
  return (
    <ol className="flow-steps" aria-label={ui("Этапы")}>
      {labels.map((label, i) => (
        <li
          key={label}
          aria-label={`${i + 1}. ${label}`}
          className={i === current ? "current" : i < current ? "done" : ""}
          aria-current={i === current ? "step" : undefined}
        >
          <span>{i < current ? <Icon name="check" size={15} /> : i + 1}</span>
          <b>{label}</b>
        </li>
      ))}
    </ol>
  );
}
export function Disclosure({
  title,
  children,
  open = false,
}: {
  title: string;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details className="disclosure" open={open || undefined}>
      <summary>
        {title}
        <Icon name="chevron" size={17} />
      </summary>
      <div>{children}</div>
    </details>
  );
}
export function AccountSelect({
  label,
  accounts,
  value,
  onChange,
  exclude,
  disabled = false,
}: {
  label: string;
  accounts: AccountBalance[];
  value: string;
  onChange: (id: string) => void;
  exclude?: string;
  disabled?: boolean;
}) {
  const { t, language } = useI18n();
  return (
    <label>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
        disabled={disabled}
      >
        <option value="">{t("Выберите счёт", "Choose an account")}</option>
        {accounts
          .filter((a) => a.id !== exclude)
          .map((a) => (
            <option key={a.id} value={a.id}>
              {a.organization_id ? t("Бизнес · ", "Business · ") : ""}
              {a.name} · {money(a.available.amount, language)} ·{" "}
              {a.public_number.slice(-4)}
            </option>
          ))}
      </select>
    </label>
  );
}
export function Help({
  term,
  children,
}: {
  term: string;
  children: ReactNode;
}) {
  return (
    <details className="context-help">
      <summary>
        <Icon name="info" size={15} />
        {term}
      </summary>
      <p>{children}</p>
    </details>
  );
}
