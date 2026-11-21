import { useId, useState } from "react";
import { useI18n } from "../i18n";

export function PasswordField({
  label,
  value,
  onChange,
  isNew = false,
  required = true,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  isNew?: boolean;
  required?: boolean;
}) {
  const id = useId(),
    [visible, setVisible] = useState(false);
  const { t } = useI18n();
  return (
    <label htmlFor={id}>
      {label}
      <span className="password-control">
        <input
          id={id}
          aria-label={label}
          name={isNew ? "new-password" : "password"}
          type={visible ? "text" : "password"}
          autoComplete={isNew ? "new-password" : "current-password"}
          value={value}
          minLength={isNew ? 12 : undefined}
          required={required}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="text-button"
          aria-label={
            (visible
              ? t("Скрыть пароль", "Hide password")
              : t("Показать пароль", "Show password")) +
            ": " +
            label
          }
          aria-pressed={visible}
          onClick={() => setVisible((v) => !v)}
        >
          {visible ? t("Скрыть", "Hide") : t("Показать", "Show")}
        </button>
      </span>
    </label>
  );
}
