import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { localError, useI18n } from "../i18n";
import type { Recipient } from "../types";
import { Avatar, Icon } from "./BankUI";
import { Notice } from "./Notice";

export function RecipientPicker({
  value,
  onChange,
  label,
}: {
  value?: Recipient;
  onChange: (recipient?: Recipient) => void;
  label?: string;
}) {
  const { t, language } = useI18n();
  const id = useId();
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Recipient[]>([]);
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
  const [active, setActive] = useState(-1);
  useEffect(() => {
    setItems([]);
    setSearched(false);
    setActive(-1);
    setError("");
    if (query.trim().length < 3 || value) {
      setBusy(false);
      return;
    }
    const abort = new AbortController();
    const timer = setTimeout(() => {
      setBusy(true);
      api
        .searchRecipients(query, abort.signal)
        .then((result) => {
          if (!abort.signal.aborted) {
            setItems(result.items);
            setSearched(true);
          }
        })
        .catch((e) => {
          if (!abort.signal.aborted) setError(localError(e, language));
        })
        .finally(() => {
          if (!abort.signal.aborted) setBusy(false);
        });
    }, 350);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [query, value, language]);
  function choose(recipient: Recipient) {
    onChange(recipient);
    setQuery("");
    setItems([]);
    setSearched(false);
  }
  function keydown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && items.length) {
      event.preventDefault();
      setActive((current) => (current + 1) % items.length);
    }
    if (event.key === "ArrowUp" && items.length) {
      event.preventDefault();
      setActive((current) => (current - 1 + items.length) % items.length);
    }
    if (event.key === "Enter" && active >= 0 && items[active]) {
      event.preventDefault();
      choose(items[active]);
    }
    if (event.key === "Escape") {
      setItems([]);
      setSearched(false);
    }
  }
  if (value)
    return (
      <div className="chosen-recipient">
        <Avatar name={value.display_name} />
        <span>
          <b>{value.display_name}</b>
          <small>
            {value.bank_username
              ? "@" + value.bank_username
              : value.public_number}
            {value.minecraft_nickname
              ? " · Minecraft: " + value.minecraft_nickname
              : ""}
          </small>
        </span>
        <span className="verified-dot">
          <Icon name="check" size={15} />
        </span>
        <button
          type="button"
          className="text-button"
          onClick={() => onChange(undefined)}
        >
          {t("Изменить", "Change")}
        </button>
      </div>
    );
  return (
    <div className="recipient-picker">
      <label htmlFor={id}>{label || t("Кому перевести", "Recipient")}</label>
      <div className="search-field">
        <Icon name="search" />
        <input
          id={id}
          role="combobox"
          aria-expanded={items.length > 0}
          aria-controls={id + "-results"}
          aria-activedescendant={active >= 0 ? id + "-" + active : undefined}
          autoComplete="off"
          value={query}
          onKeyDown={keydown}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t(
            "Банковское имя, Minecraft-ник или номер счёта",
            "Bank username, Minecraft name or account number",
          )}
        />
        {busy && <span className="spinner small" />}
      </div>
      <small className="field-hint">
        {t(
          "Не менее 3 символов. Выберите получателя — не только похожее имя.",
          "At least 3 characters. Select the intended recipient, not just a similar name.",
        )}
      </small>
      <Notice error={error} />
      {items.length > 0 && (
        <div
          className="recipient-results"
          role="listbox"
          aria-label={t("Найденные получатели", "Recipient search results")}
          id={id + "-results"}
        >
          {items.map((item, index) => (
            <button
              type="button"
              role="option"
              aria-selected={active === index}
              id={id + "-" + index}
              key={item.account_id}
              onClick={() => choose(item)}
            >
              <Avatar name={item.display_name} />
              <span>
                <b>{item.display_name}</b>
                <small>
                  {item.bank_username
                    ? "@" + item.bank_username
                    : item.public_number}
                  {item.minecraft_nickname
                    ? " · Minecraft: " + item.minecraft_nickname
                    : ""}
                  {item.discord_username
                    ? " · Discord: " + item.discord_username
                    : ""}
                </small>
                <small>{item.public_number}</small>
              </span>
              <Icon name="chevron" size={16} />
            </button>
          ))}
        </div>
      )}
      {searched && !busy && items.length === 0 && !error && (
        <p className="inline-empty">
          {t(
            "Получатель не найден. Проверьте имя или попросите его публичные реквизиты.",
            "Recipient not found. Check the username or ask for their public account details.",
          )}
        </p>
      )}
    </div>
  );
}
