import { Icon, PageHeading } from "../components/BankUI";
import { useI18n } from "../i18n";
import { Keys } from "./Keys";

export function Developers() {
  const { t } = useI18n();
  return (
    <>
      <PageHeading
        title={t("Для разработчиков", "For developers")}
        subtitle={t(
          "Подключите магазин, игровой сервер или свою интеграцию",
          "Connect your shop, game server or integration",
        )}
      />
      <div className="developer-intro">
        <Icon name="code" size={36} />
        <div>
          <h2>
            Banking System API <span className="tag">v1 · 2.6.1</span>
          </h2>
          <p>
            {t(
              "Один REST API, те же финансовые гарантии. Обычным клиентам банка ключи не нужны.",
              "One REST API, the same financial guarantees. Everyday banking does not require API keys.",
            )}
          </p>
        </div>
        <a
          className="button-link"
          href="/api/docs"
          target="_blank"
          rel="noreferrer"
        >
          {t("Открыть документацию", "Open documentation")} ↗
        </a>
      </div>
      <div className="developer-grid">
        <section className="panel">
          <h3>{t("Первый запрос", "Your first request")}</h3>
          <pre>
            {
              'curl "$HORIZON_API_URL/api/v1/accounts" \\\n  -H "Authorization: Bearer $HORIZON_API_KEY"'
            }
          </pre>
          <p>
            {t(
              "Секрет берите из защищённой переменной окружения. Он не должен попадать в URL, логи или клиентский JavaScript.",
              "Read the secret from a protected environment variable. Never put it in URLs, logs or client-side JavaScript.",
            )}
          </p>
        </section>
        <section className="panel">
          <h3>{t("Что важно знать", "Important details")}</h3>
          <ul>
            <li>
              {t(
                "Сумма — строка: 125.50, без float.",
                "Amount is a string: 125.50, never float.",
              )}
            </li>
            <li>
              {t(
                "Повтор перевода сохраняет Idempotency-Key и все исходные данные.",
                "A transfer retry preserves its Idempotency-Key and original payload.",
              )}
            </li>
            <li>
              {t(
                "Scopes не заменяют разрешения на конкретный счёт.",
                "Scopes never replace permission on the specific account.",
              )}
            </li>
            <li>
              {t(
                "401 — авторизация, 403 — права, 409 — состояние, 429 — частота.",
                "401: authentication, 403: permissions, 409: state, 429: rate limit.",
              )}
            </li>
          </ul>
          <a href="/api/openapi.json" target="_blank" rel="noreferrer">
            OpenAPI JSON ↗
          </a>
        </section>
      </div>
      <Keys />
    </>
  );
}
