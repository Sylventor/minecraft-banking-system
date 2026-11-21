import { localize as ui } from "../i18n";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { useBank } from "../bank-context";
import { Icon, Modal } from "../components/BankUI";
import { Notice } from "../components/Notice";
import { localError, useI18n } from "../i18n";
import type { ApiKey, ApiKeyCreated, Organization } from "../types";
const scopeNames = (): [string, string, string][] => [
  ["profile.read", ui("Чтение профиля"), "Read profile"],
  ["accounts.read", ui("Список счетов"), "List accounts"],
  ["balance.read", ui("Просмотр баланса"), "Read balance"],
  ["history.read", ui("История операций"), "Read activity"],
  [
    "analytics.read",
    ui("Аналитика организаций"),
    "Read organization analytics",
  ],
  ["exchange.read", ui("Чтение портфеля и заявок"), "Read investments"],
  ["exchange.trade", ui("Торговля акциями"), "Trade securities"],
  ["analytics.export", ui("Экспорт аналитики"), "Export analytics"],
  ["recipients.read", ui("Поиск получателей"), "Search recipients"],
  ["transfers.create", ui("Отправка переводов"), "Send transfers"],
  ["payments.create", ui("Создание платежей"), "Create payments"],
  ["holds.create", ui("Блокировка средств"), "Reserve funds"],
  [
    "organizations.manage",
    ui("Создание и список организаций"),
    "Create and list organizations",
  ],
];
export function Keys() {
  const { data } = useBank();
  const { t, language } = useI18n();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [organization, setOrganization] = useState("");
  const [name, setName] = useState("");
  const [accounts, setAccounts] = useState<string[]>([]);
  const [scopes, setScopes] = useState(["accounts.read", "balance.read"]);
  const [created, setCreated] = useState<ApiKeyCreated>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [revoking, setRevoking] = useState<ApiKey>();
  useEffect(() => {
    api
      .organizations()
      .then(setOrganizations)
      .catch(() => undefined);
    api
      .keys()
      .then(setKeys)
      .catch((e) => setError(localError(e, language)));
  }, [language]);
  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setCreated(
        await api.createKey(name, scopes, accounts, organization || undefined),
      );
      setKeys(await api.keys());
      setName("");
      setOpening(false);
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function revoke() {
    if (!revoking) return;
    setBusy(true);
    try {
      await api.revokeKey(revoking.id);
      setKeys(await api.keys());
      setRevoking(undefined);
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>{t("API-ключи", "API keys")}</h2>
          <p>
            {t(
              "Доступ для ваших серверных интеграций",
              "Access for your server integrations",
            )}
          </p>
        </div>
        <button onClick={() => setOpening(true)}>
          <Icon name="plus" size={17} />
          {t("Создать ключ", "Create key")}
        </button>
      </div>
      <Notice error={error} />
      {created && (
        <div className="key-secret" role="status">
          <strong>
            {t(
              "Сохраните секрет сейчас. Он показывается только один раз.",
              "Save this secret now. It is displayed only once.",
            )}
          </strong>
          <code>{created.secret}</code>
          <small>
            {t(
              "Не вставляйте секрет в frontend, URL, Discord и публичные репозитории.",
              "Never put this secret in frontend code, URLs, Discord or public repositories.",
            )}
          </small>
          <button className="secondary" onClick={() => setCreated(undefined)}>
            {t("Я сохранил секрет", "I saved the secret")}
          </button>
        </div>
      )}
      <div className="key-list">
        {keys.map((key) => (
          <div key={key.id}>
            <span>
              <b>{key.name}</b>
              <small>
                {key.prefix} · {key.scopes.join(", ")}
              </small>
            </span>
            <span className="tag">
              {key.revoked_at
                ? t("Отозван", "Revoked")
                : t("Активен", "Active")}
            </span>
            {!key.revoked_at && (
              <button
                className="text-button danger"
                onClick={() => setRevoking(key)}
              >
                {t("Отозвать", "Revoke")}
              </button>
            )}
          </div>
        ))}
      </div>
      {!keys.length && (
        <p className="muted spaced">
          {t(
            "Ключей пока нет. Для обычных переводов API-ключ не нужен.",
            "No keys yet. You do not need an API key for everyday transfers.",
          )}
        </p>
      )}
      {opening && (
        <Modal
          title={t("Новый API-ключ", "New API key")}
          onClose={() => {
            if (!busy) setOpening(false);
          }}
        >
          <Notice error={error} />
          <form onSubmit={(event) => void create(event)}>
            <label>
              {ui("Тип ключа")}
              <select
                value={organization}
                onChange={(e) => {
                  setOrganization(e.target.value);
                  setAccounts([]);
                  setScopes(
                    e.target.value
                      ? [
                          "payment_requests.create",
                          "payment_requests.read",
                          "payment_requests.cancel",
                        ]
                      : ["accounts.read", "balance.read"],
                  );
                }}
              >
                <option value="">{ui("Личная интеграция")}</option>
                {organizations.map((org) => (
                  <option key={org.id} value={org.id}>
                    {ui("Магазин: ")}
                    {org.name}
                  </option>
                ))}
              </select>
            </label>
            {organization && (
              <p>
                {ui(
                  "Этот ключ сможет выставлять и проверять свои запросы, но не списывать деньги. Нужны права владельца или администратора организации.",
                )}
              </p>
            )}
            <label>
              {t("Название интеграции", "Integration name")}
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={100}
                required
              />
            </label>
            <fieldset>
              <legend>{t("Доступные счета", "Permitted accounts")}</legend>
              {data.accounts
                .filter(
                  (a) => !organization || a.organization_id === organization,
                )
                .map((account) => (
                  <label className="checkbox" key={account.id}>
                    <input
                      type="checkbox"
                      checked={accounts.includes(account.id)}
                      onChange={(e) =>
                        setAccounts((value) =>
                          e.target.checked
                            ? [...value, account.id]
                            : value.filter((id) => id !== account.id),
                        )
                      }
                    />
                    {account.name}
                    <small>{account.public_number}</small>
                  </label>
                ))}
              <p className="field-hint">
                {t(
                  "Без выбранных счетов ключ не сможет читать балансы и отправлять средства.",
                  "Without selected accounts, the key cannot read balances or send funds.",
                )}
              </p>
            </fieldset>
            <fieldset>
              <legend>
                {t(
                  "Минимально необходимые разрешения",
                  "Minimum necessary permissions",
                )}
              </legend>
              <div className="scope-list">
                {(organization
                  ? [
                      [
                        "payment_requests.create",
                        ui("Выставлять запросы"),
                        "Create requests",
                      ],
                      [
                        "payment_requests.read",
                        ui("Проверять свои запросы"),
                        "Read requests",
                      ],
                      [
                        "payment_requests.cancel",
                        ui("Отменять запросы"),
                        "Cancel requests",
                      ],
                      [
                        "recipients.read",
                        ui("Поиск плательщиков"),
                        "Find payers",
                      ],
                    ]
                  : scopeNames()
                ).map(([scope, ru, en]) => (
                  <label className="checkbox" key={scope}>
                    <input
                      type="checkbox"
                      checked={scopes.includes(scope)}
                      onChange={(e) =>
                        setScopes((value) =>
                          e.target.checked
                            ? [...value, scope]
                            : value.filter((item) => item !== scope),
                        )
                      }
                    />
                    <span>
                      {t(ru, en)}
                      <small>{scope}</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            <button disabled={busy || scopes.length === 0}>
              {t("Создать ключ", "Create key")}
            </button>
          </form>
        </Modal>
      )}
      {revoking && (
        <Modal
          title={t("Отозвать ключ?", "Revoke key?")}
          onClose={() => {
            if (!busy) setRevoking(undefined);
          }}
        >
          <Notice error={error} />
          <p>
            {t("Интеграция «", "Integration “")}
            {revoking.name}
            {t(
              "» больше не сможет отправлять новые запросы с этим секретом.",
              "” will no longer be able to make new requests with this secret.",
            )}
          </p>
          <button
            className="danger-button"
            disabled={busy}
            onClick={() => void revoke()}
          >
            {t("Подтвердить отзыв", "Confirm revocation")}
          </button>
        </Modal>
      )}
    </section>
  );
}
