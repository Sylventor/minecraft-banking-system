import { useI18n as useLocale } from "../i18n";
import { localError, normalizeLanguage, localizeSystem } from "../i18n";
export function Notice({ error }: { error: string }) {
  useLocale();
  return error ? (
    <p role="alert" className="error">
      {localizeSystem(error)}
    </p>
  ) : null;
}
export function errorMessage(error: unknown): string {
  return localError(
    error,
    normalizeLanguage(localStorage.getItem("horizon.language")),
  );
}
