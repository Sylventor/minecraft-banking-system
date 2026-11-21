import { useEffect, useRef, useState, type FormEvent } from "react";
import { request } from "../api";
import { useBank } from "../bank-context";
import { Loading } from "../components/BankUI";
import { PasswordField } from "../components/PasswordField";
import { Notice } from "../components/Notice";
import { localError, localizeSystem, useI18n } from "../i18n";
type Methods = {
  login: string | null;
  password_enabled: boolean;
  discord: {
    username: string;
  } | null;
  recent: boolean;
  auth_method: string;
};
export function AuthenticationMethods() {
  const { t } = useI18n(),
    { refresh } = useBank();
  const [methods, setMethods] = useState<Methods>(),
    [mode, setMode] = useState(""),
    [login, setLogin] = useState("");
  const [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState(""),
    [current, setCurrent] = useState("");
  const outcome = new URLSearchParams(location.hash.split("?")[1]).get("auth");
  const [error, setError] = useState(
    outcome === "discord-conflict"
      ? t(
          "Discord уже связан с другим аккаунтом либо у вас привязан другой Discord. Аккаунты не объединены.",
          "That Discord is linked elsewhere or another Discord is already linked here. Accounts have not been merged.",
        )
      : "",
  );
  const [success, setSuccess] = useState(
      outcome === "discord-verified"
        ? t("Вход подтверждён через Discord", "Identity confirmed with Discord")
        : outcome === "discord-linked"
          ? t(
              "Discord привязан к вашему аккаунту",
              "Discord linked to your account",
            )
          : "",
    ),
    [busy, setBusy] = useState(false);
  const pending = useRef(false);
  useEffect(() => {
    let active = true;
    request<Methods>("/auth/methods")
      .then((v) => {
        if (active) setMethods(v);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, []);
  function open(value: string) {
    setMode(value);
    setPassword("");
    setConfirmation("");
    setCurrent("");
    setError("");
    setSuccess("");
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    if ([...password].length > 1024 || [...current].length > 1024) {
      setError(
        t(
          "Пароль длиннее 1024 символов. Выберите пароль в пределах лимита: введённое значение не обрезается.",
          "Password exceeds 1024 characters. Choose a password within the limit; your input is not truncated.",
        ),
      );
      return;
    }
    if ((mode === "setup" || mode === "change") && password !== confirmation) {
      setError(t("Пароли не совпадают", "Passwords do not match"));
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const path =
        mode === "setup"
          ? "/password/setup"
          : mode === "change"
            ? "/password/change"
            : mode === "unlink"
              ? "/discord/unlink"
              : "/reauth";
      const body =
        mode === "setup"
          ? { login, password, password_confirmation: confirmation }
          : mode === "change"
            ? {
                password,
                password_confirmation: confirmation,
                ...(current ? { current_password: current } : {}),
              }
            : { password: current };
      await request("/auth" + path, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setMode("");
      setPassword("");
      setConfirmation("");
      setCurrent("");
      setMethods(await request<Methods>("/auth/methods"));
      await refresh();
      setSuccess(
        t(
          "Способы входа обновлены. При изменении пароля или отвязке остальные сессии завершены.",
          "Sign-in settings updated. Password changes or unlinking also end your other sessions.",
        ),
      );
    } catch (e) {
      setError(localError(e));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function link(reauthenticating = false) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await request<{
        url: string;
      }>(reauthenticating ? "/auth/discord/reauth" : "/auth/discord/link", {
        method: "POST",
      });
      location.assign(value.url);
    } catch (e) {
      setError(localError(e));
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="panel auth-methods">
      <h2>{t("Способы входа", "Sign-in methods")}</h2>
      <Notice error={error} />
      {success && (
        <p className="success" role="status">
          {localizeSystem(success)}
        </p>
      )}
      {!methods ? (
        !error && <Loading />
      ) : (
        <>
          <div className="auth-method-row">
            <div>
              <h3>{t("Логин и пароль", "Username and password")}</h3>
              <p>
                {methods.password_enabled
                  ? methods.login
                  : t("Не настроены", "Not configured")}
              </p>
              <small>
                {t(
                  "Логин для входа независим от публичного банковского имени.",
                  "Your sign-in username is independent of your public bank name.",
                )}
              </small>
            </div>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                open(methods.password_enabled ? "change" : "setup")
              }
            >
              {methods.password_enabled
                ? t("Изменить пароль", "Change password")
                : t("Добавить вход по паролю", "Add password sign-in")}
            </button>
          </div>
          <div className="auth-method-row">
            <div>
              <h3>Discord</h3>
              <p>
                {methods.discord
                  ? t("Привязан: ", "Linked: ") + methods.discord.username
                  : t("Не привязан", "Not linked")}
              </p>
            </div>
            {methods.discord ? (
              <button
                className="secondary danger"
                disabled={busy || !methods.password_enabled}
                onClick={() => open("unlink")}
              >
                {t("Отвязать Discord", "Unlink Discord")}
              </button>
            ) : (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void link()}
              >
                {t("Привязать Discord", "Link Discord")}
              </button>
            )}
          </div>
          {methods.discord && !methods.password_enabled && (
            <p className="info-box">
              {t(
                "Discord — единственный способ входа. Сначала добавьте пароль, чтобы можно было отвязать Discord.",
                "Discord is your only sign-in method. Add a password before unlinking Discord.",
              )}
            </p>
          )}
          <div className="payment-tabs">
            {methods.password_enabled && (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => open("reauth")}
              >
                {t("Подтвердить вход паролем", "Confirm with password")}
              </button>
            )}
            {methods.discord && (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void link(true)}
              >
                {t("Подтвердить вход через Discord", "Confirm with Discord")}
              </button>
            )}
          </div>
          {!methods.recent && (
            <p className="info-box">
              {t(
                "Перед добавлением способа входа подтвердите личность заново.",
                "Confirm your identity again before adding a sign-in method.",
              )}
            </p>
          )}
          {mode && (
            <form onSubmit={submit} className="auth-method-form">
              {mode === "setup" && (
                <>
                  <h3>
                    {t("Добавить вход по паролю", "Add password sign-in")}
                  </h3>
                  <p>
                    {t(
                      "Для установки нужен недавний вход через Discord. Существующие счета и активы сохранятся.",
                      "A recent Discord sign-in is required. Your existing accounts and assets stay with this profile.",
                    )}
                  </p>
                  <label>
                    {t("Логин", "Username")}
                    <input
                      autoComplete="username"
                      name="username"
                      required
                      minLength={3}
                      maxLength={24}
                      pattern="[A-Za-z][A-Za-z0-9_]{2,23}"
                      value={login}
                      onChange={(e) => setLogin(e.target.value)}
                    />
                  </label>
                </>
              )}
              {(mode === "reauth" ||
                mode === "unlink" ||
                mode === "change") && (
                <PasswordField
                  label={t("Текущий пароль", "Current password")}
                  value={current}
                  onChange={setCurrent}
                  required={
                    !(
                      mode === "change" &&
                      methods.auth_method === "discord" &&
                      methods.recent
                    )
                  }
                />
              )}
              {mode === "change" &&
                methods.auth_method === "discord" &&
                methods.recent && (
                  <p className="info-box">
                    {t(
                      "После недавнего Discord-входа текущий пароль можно оставить пустым — это безопасное восстановление доступа.",
                      "After a recent Discord sign-in you may leave the current password empty to recover password access.",
                    )}
                  </p>
                )}
              {(mode === "setup" || mode === "change") && (
                <>
                  <PasswordField
                    label={t("Новый пароль", "New password")}
                    value={password}
                    onChange={setPassword}
                    isNew
                  />
                  <small>
                    {t(
                      "12–1024 символа. Парольные фразы и менеджеры паролей поддерживаются.",
                      "12–1024 characters. Passphrases and password managers are supported.",
                    )}
                  </small>
                  <PasswordField
                    label={t("Повтор пароля", "Repeat password")}
                    value={confirmation}
                    onChange={setConfirmation}
                    isNew
                  />
                </>
              )}
              {mode === "unlink" && (
                <p className="info-box">
                  {t(
                    "Discord-вход и Discord-уведомления перестанут работать; доступ бота к этому профилю также прекратится. Счета и история сохранятся. Подтвердите действие текущим паролем.",
                    "Discord sign-in, notifications and bot access to this profile will stop. Your accounts and history remain. Confirm using your current password.",
                  )}
                </p>
              )}
              <div className="flow-footer">
                <button disabled={busy}>
                  {busy
                    ? t("Проверяем…", "Please wait…")
                    : mode === "unlink"
                      ? t("Подтвердить отвязку", "Confirm unlinking")
                      : t("Сохранить", "Save")}
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => open("")}
                >
                  {t("Отмена", "Cancel")}
                </button>
              </div>
            </form>
          )}
          {!methods.discord && (
            <p className="muted">
              {t(
                "Без привязанного Discord автоматическое восстановление забытого пароля недоступно. Используйте менеджер паролей.",
                "Without a linked Discord, automatic recovery of a forgotten password is unavailable. Use a password manager.",
              )}
            </p>
          )}
        </>
      )}
    </section>
  );
}
