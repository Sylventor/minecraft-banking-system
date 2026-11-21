import { localize as ui } from "../i18n";
import { Suspense, lazy, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { useBank } from "../bank-context";
import {
  Avatar,
  Icon,
  Loading,
  Modal,
  PageHeading,
} from "../components/BankUI";
import { Notice } from "../components/Notice";
import { dateTime, localError, useI18n, type Language } from "../i18n";
import type { Session } from "../types";
import { MinecraftLink } from "./MinecraftLink";
import { NotificationSettings } from "./NotificationSettings";
import { AuthenticationMethods } from "./AuthenticationMethods";
import { ThemeChoice } from "../theme";
import { Disclosure, tabKeys } from "../components/Flow";
const Keys = lazy(() => import("./Keys").then((m) => ({ default: m.Keys })));
export function Settings() {
  const { data, refresh } = useBank();
  const { t, language, setLanguage } = useI18n();
  const profile = data.profile;
  const [tab, setTab] = useState(() => {
    const value = new URLSearchParams(location.hash.split("?")[1]).get("tab");
    return value &&
      ["profile", "appearance", "security", "notifications", "keys"].includes(
        value,
      )
      ? value
      : "profile";
  });
  const [name, setName] = useState(profile.display_name);
  const [username, setUsername] = useState(profile.bank_username ?? "");
  const [preferredLanguage, setPreferredLanguage] = useState<Language>(
    profile.language,
  );
  const [primary, setPrimary] = useState(data.primary_account.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionError, setSessionError] = useState("");
  const [revoking, setRevoking] = useState<Session>();
  const [revokingOthers, setRevokingOthers] = useState(false);
  useEffect(() => {
    let active = true;
    if (tab === "security")
      api
        .sessions()
        .then((value) => {
          if (active) setSessions(value);
        })
        .catch((e) => {
          if (active) setSessionError(localError(e, language));
        });
    return () => {
      active = false;
    };
  }, [language, tab]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      if (primary !== data.primary_account.id)
        await api.updateAccount(primary, { is_primary: true });
      await api.updateSettings({
        display_name: name,
        bank_username: username,
        language: preferredLanguage,
      });
      setLanguage(preferredLanguage);
      await refresh();
      setSaved(true);
    } catch (e) {
      setError(localError(e, language));
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }
  async function revoke() {
    if (!revoking) return;
    setBusy(true);
    try {
      await api.revokeSession(revoking.id);
      setSessions(await api.sessions());
      setRevoking(undefined);
    } catch (e) {
      setSessionError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeading
        title={t("Настройки", "Settings")}
        subtitle={t(
          "Ваш профиль, связанные аккаунты и безопасность",
          "Your profile, linked identities and security",
        )}
      />
      <div
        className="page-tabs"
        role="tablist"
        onKeyDown={tabKeys}
        aria-label={t("Разделы настроек", "Settings sections")}
      >
        {Object.entries({
          profile: t("Профиль", "Profile"),
          appearance: t("Оформление", "Appearance"),
          security: t("Безопасность", "Security"),
          notifications: t("Уведомления", "Notifications"),
          keys: t("API-ключи", "API keys"),
        }).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            disabled={busy}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <Notice error={error} />
      {saved && (
        <p className="success-banner" role="status">
          <Icon name="check" />
          {t("Изменения сохранены", "Changes saved")}
        </p>
      )}
      {tab === "profile" && (
        <div className="settings-layout">
          <form
            className="panel settings-profile"
            onSubmit={(event) => void save(event)}
          >
            <div className="profile-heading">
              <Avatar name={profile.display_name || "H"} large />
              <div>
                <h2>{profile.display_name}</h2>
                <small>
                  {profile.bank_username
                    ? "@" + profile.bank_username
                    : t("Клиент Banking System", "Banking System customer")}
                </small>
              </div>
            </div>
            <label>
              {t("Отображаемое имя", "Display name")}
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={100}
                required
              />
            </label>
            <label>
              {t("Банковское имя", "Bank username")}
              <div className="prefix-input">
                <span>@</span>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  pattern="[A-Za-z][A-Za-z0-9_]{2,23}"
                  minLength={3}
                  maxLength={24}
                  required
                />
              </div>
              <small>
                {t(
                  "Уникально без учёта регистра. По нему вас смогут найти и отправить перевод.",
                  "Case-insensitive unique username. Others can use it to find you and send a transfer.",
                )}
              </small>
            </label>
            <label>
              {t("Основной счёт", "Primary account")}
              <select
                value={primary}
                onChange={(e) => setPrimary(e.target.value)}
              >
                {data.accounts
                  .filter(
                    (a) =>
                      a.owner_id === profile.id &&
                      a.account_type === "personal" &&
                      a.status === "active",
                  )
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </select>
              <small>
                {t(
                  "Для переводов и бота по умолчанию. Средства между счетами не перемещаются.",
                  "Default for transfers and the bot. Changing it does not move funds.",
                )}
              </small>
            </label>
            <label>
              {t("Язык интерфейса", "Interface language")}
              <select
                value={preferredLanguage}
                onChange={(e) =>
                  setPreferredLanguage(e.target.value as Language)
                }
              >
                <option value="ru">Русский</option>
                <option value="en">English</option>
                <option value="uk-UA">Українська</option>
              </select>
            </label>
            <button disabled={busy}>
              {busy
                ? t("Сохраняем…", "Saving…")
                : t("Сохранить изменения", "Save changes")}
            </button>
          </form>
          <div className="settings-aside">
            <section className="panel">
              <div className="section-heading">
                <h3>Discord</h3>
                <button
                  className="text-button"
                  onClick={() => setTab("security")}
                >
                  {t("Способы входа", "Sign-in methods")}
                </button>
                <span className="tag positive">
                  {data.profile.discord
                    ? t("Подтверждён", "Verified")
                    : t("Не привязан", "Not linked")}
                </span>
              </div>
              <p>
                {profile.discord?.username ||
                  t("Discord не привязан", "Discord not linked")}
              </p>
              <small>
                {data.profile.discord &&
                  t(
                    "Связан через безопасный OAuth2. Discord ID не используется как пароль.",
                    "Linked through secure OAuth2. Discord ID is not a password.",
                  )}
              </small>
            </section>
            <MinecraftLink />
          </div>
        </div>
      )}
      {tab === "appearance" && (
        <section className="panel appearance-panel">
          <h2>{t("Ваш Banking System", "Your Banking System")}</h2>
          <p className="muted">
            {t(
              "Выберите комфортное оформление. Настройка сохранится на этом устройстве, в том числе после повторного входа.",
              "Choose a comfortable appearance. Your preference stays on this device, including after signing in again.",
            )}
          </p>
          <div className="theme-preview" aria-hidden="true">
            <i />
            <div>
              <b />
              <span />
              <span />
            </div>
          </div>
          <ThemeChoice />
          <p className="field-hint">
            {t(
              "Режим «Как на устройстве» следует системной теме. Анимации учитывают системную настройку уменьшения движения.",
              "System mode follows your device appearance. Animations respect reduced motion preferences.",
            )}
          </p>
        </section>
      )}
      {tab === "notifications" && <NotificationSettings />}
      {tab === "keys" && (
        <Suspense fallback={<Loading />}>
          <Keys />
          <p className="field-hint">
            <a href="/api/docs">{t("Документация API", "API documentation")}</a>
          </p>
        </Suspense>
      )}
      {tab === "security" && (
        <>
          <AuthenticationMethods />
          <section className="panel sessions-panel">
            <div className="section-heading">
              <h2>
                {t(
                  "Безопасность → Активные сессии",
                  "Security → Active sessions",
                )}
              </h2>
              <Icon name="shield" />
            </div>
            <Notice error={sessionError} />
            <p>
              {ui(
                "Сессия продлевается при активности до 30 дней бездействия, максимум — 90 дней от входа. Для чувствительных административных операций потребуется повторный вход.",
              )}
            </p>
            <div className="payment-tabs">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => setRevokingOthers(true)}
              >
                {ui("Завершить все остальные сессии")}
              </button>
              <Disclosure
                title={t(
                  "Дополнительные инструменты сессии",
                  "Advanced session tools",
                )}
              >
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api.rotateSession();
                      setSessions(await api.sessions());
                    } catch (e) {
                      setSessionError(localError(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  {ui("Обновить токен этой сессии")}
                </button>
              </Disclosure>
            </div>
            {sessions.map((session) => (
              <div className="session-row" key={session.id}>
                <span className="account-icon">
                  <Icon name="shield" />
                </span>
                <span>
                  <b>
                    {session.current
                      ? t("Эта сессия", "This session")
                      : t("Другая сессия", "Another session")}
                  </b>
                  <small>
                    {t("Вход: ", "Signed in: ")}
                    {dateTime(session.created_at, language)} ·{" "}
                    {t("До ", "Until ")}
                    {dateTime(session.expires_at, language)}
                    {session.last_activity_at && (
                      <>
                        {" "}
                        {ui("· Последняя активность:")}{" "}
                        {dateTime(session.last_activity_at, language)}
                      </>
                    )}
                    {session.client_info && (
                      <>
                        <br />
                        {ui("Клиент: ")}
                        {session.client_info}
                      </>
                    )}
                  </small>
                </span>
                {session.current ? (
                  <span className="tag">{t("Текущая", "Current")}</span>
                ) : (
                  <button
                    className="text-button danger"
                    onClick={() => setRevoking(session)}
                  >
                    {t("Завершить", "End session")}
                  </button>
                )}
              </div>
            ))}
          </section>
        </>
      )}
      {revokingOthers && (
        <Modal
          title={ui("Завершить все остальные сессии?")}
          onClose={() => {
            if (!busy) setRevokingOthers(false);
          }}
        >
          <Notice error={sessionError} />
          <p>
            {ui(
              "Текущий вход сохранится. Все другие токены будут немедленно отозваны.",
            )}
          </p>
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.revokeOtherSessions();
                setSessions(await api.sessions());
                setRevokingOthers(false);
              } catch (e) {
                setSessionError(localError(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {ui("Подтвердить завершение остальных")}
          </button>
        </Modal>
      )}
      {revoking && (
        <Modal
          title={t("Завершить сессию?", "End this session?")}
          onClose={() => setRevoking(undefined)}
        >
          <p>
            {t(
              "На другом устройстве потребуется снова войти в аккаунт.",
              "The other device will need to sign in again.",
            )}
          </p>
          <button
            className="danger-button"
            disabled={busy}
            onClick={() => void revoke()}
          >
            {t("Завершить сессию", "End session")}
          </button>
        </Modal>
      )}
    </>
  );
}
