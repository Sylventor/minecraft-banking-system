import { useI18n } from "../i18n";

export function LanguageChoice({ className }: { className?: string }) {
  const { language, setLanguage, t } = useI18n();
  return (
    <select
      className={className}
      aria-label={t("Язык интерфейса", "Interface language")}
      value={language}
      onChange={(event) => setLanguage(event.target.value as typeof language)}
    >
      <option value="ru">Русский</option>
      <option value="en">English</option>
      <option value="uk-UA">Українська</option>
    </select>
  );
}
