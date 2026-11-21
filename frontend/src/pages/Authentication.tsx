import { serverUrl } from "../branding";
import { localize as ui, useI18n as useLocale } from "../i18n";
import { useRef, useState, type FormEvent } from "react";
import { api, ApiFailure, request } from "../api";
import { Brand, Icon } from "../components/BankUI";
import { PasswordField } from "../components/PasswordField";
import { Notice } from "../components/Notice";
import { Disclosure } from "../components/Flow";
import { localError, useI18n } from "../i18n";
import { ThemeChoice } from "../theme";
import { LanguageChoice } from "../components/LanguageChoice";
export function ServerLink() {
  const { t } = useLocale();
  const label = t("Minecraft-сервер", "Minecraft server", "Minecraft-сервер");
  return serverUrl ? (
    <a href={serverUrl} target="_blank" rel="noopener noreferrer">
      {label} ↗
    </a>
  ) : (
    <span>{label}</span>
  );
}
export function Authentication({
  error: initialError,
  onAuthenticated,
}: {
  error: string;
  onAuthenticated: () => Promise<void>;
}) {
  const { t, language } = useI18n();
  const registering = location.pathname.replace(/\/$/, "") === "/register";
  const [login, setLogin] = useState(""),
    [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState(initialError),
    [busy, setBusy] = useState(false),
    [checking, setChecking] = useState(false);
  const [availability, setAvailability] = useState<{
    login: string;
    available: boolean;
  }>();
  const pending = useRef(false);
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const currentAvailability =
    availability?.login === login.trim().toLowerCase()
      ? availability
      : undefined;
  async function checkLogin() {
    setChecking(true);
    setError("");
    try {
      const result = await request<{
        available: boolean;
      }>("/auth/login-availability?login=" + encodeURIComponent(login.trim()));
      setAvailability({
        login: login.trim().toLowerCase(),
        available: result.available,
      });
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setChecking(false);
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    if (registering && !ageConfirmed) {
      setError(ui("Подтвердите, что вам исполнилось 15 лет"));
      return;
    }
    if (
      [...password].length > 1024 ||
      (registering && [...password].length < 12)
    ) {
      setError(
        t(
          "Пароль должен содержать от 12 до 1024 символов при регистрации и не более 1024 при входе. Он не обрезается.",
          "Use 12–1024 characters when registering and no more than 1024 when signing in. Passwords are never truncated.",
        ),
      );
      return;
    }
    if (registering && password !== confirmation) {
      setError(t("Пароли не совпадают", "Passwords do not match"));
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await request(registering ? "/auth/register" : "/auth/login", {
        method: "POST",
        body: JSON.stringify({
          login,
          password,
          ...(registering
            ? {
                password_confirmation: confirmation,
                age_confirmed: ageConfirmed,
              }
            : {}),
        }),
      });
      setPassword("");
      setConfirmation("");
      if (
        ["/register", "/login"].includes(location.pathname.replace(/\/$/, ""))
      )
        history.replaceState({}, "", "/");
      await onAuthenticated();
    } catch (e) {
      setError(
        registering && e instanceof ApiFailure && [503, 504].includes(e.status)
          ? t(
              "Банк временно недоступен. Если логин уже создан, войдите с этим логином и паролем — повторная регистрация не нужна.",
              "Bank temporarily unavailable. If your username was created, sign in with it and your password; do not register again.",
            )
          : e instanceof ApiFailure && [503, 504].includes(e.status)
            ? t(
                "Банк временно недоступен. Повторите вход немного позже.",
                "Bank temporarily unavailable. Please try signing in again shortly.",
              )
            : localError(e, language),
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <div className="login-page auth-page">
      <header>
        <Brand />
        <ThemeChoice compact />
        <LanguageChoice className="text-button" />
      </header>
      <main className="login-content">
        <div className="login-copy">
          <span className="eyebrow">
            {t("ВАШ МИР. ВАШ БАНК.", "YOUR WORLD. YOUR BANK.")}
          </span>
          <h2 className="auth-hero-heading">
            {t(
              "Всё для вашей\nигровой экономики.",
              "Your world.\nYour game economy.",
            )}
          </h2>
          <p>
            {t(
              "Переводы друзьям, счета организаций и акции игровых компаний. Начните с логина и пароля — Discord можно привязать позже.",
              "Send to friends, manage organizations and trade virtual company shares. Start with a username and password; link Discord whenever you choose.",
            )}
          </p>
          <div className="login-benefits">
            <span>
              <Icon name="wallet" />
              {t("Личный игровой счёт", "Your personal game account")}
            </span>
            <span>
              <Icon name="shield" />
              {t(
                "Один профиль — несколько способов входа",
                "One profile, multiple ways to sign in",
              )}
            </span>
          </div>
        </div>
        <section className="login-card">
          <h1>
            {registering
              ? t(
                  "Создать аккаунт Banking System",
                  "Create your Banking System account",
                )
              : t("Войти в Banking System", "Sign in to Banking System")}
          </h1>
          <Notice error={error} />
          <form onSubmit={submit}>
            <label>
              {t("Логин", "Username")}
              <input
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                minLength={registering ? 3 : undefined}
                maxLength={24}
                pattern={registering ? "[A-Za-z][A-Za-z0-9_]{2,23}" : undefined}
                value={login}
                onChange={(e) => {
                  setLogin(e.target.value);
                  setAvailability(undefined);
                }}
              />
            </label>
            {registering && (
              <>
                <small>
                  {t(
                    "3–24 латинских символа, цифры или _. Начинается с буквы; регистр не важен.",
                    "3–24 Latin letters, digits or _. Starts with a letter; case insensitive.",
                  )}
                </small>
                <button
                  type="button"
                  className="text-button"
                  disabled={checking || busy || login.trim().length < 3}
                  onClick={() => void checkLogin()}
                >
                  {checking
                    ? t("Проверяем…", "Checking…")
                    : t("Проверить логин", "Check username")}
                </button>
                {currentAvailability && (
                  <p
                    role="status"
                    className={
                      currentAvailability.available ? "success" : "error"
                    }
                  >
                    {currentAvailability.available
                      ? t("Логин доступен", "Username available")
                      : t(
                          "Этот логин недоступен",
                          "This username is unavailable",
                        )}
                  </p>
                )}
              </>
            )}
            <PasswordField
              label={t("Пароль", "Password")}
              value={password}
              onChange={setPassword}
              isNew={registering}
            />
            {registering && (
              <>
                <small>
                  {t(
                    "От 12 до 1024 символов. Можно использовать длинную парольную фразу, пробелы и менеджер паролей.",
                    "12–1024 characters. Passphrases, spaces and password managers are welcome.",
                  )}
                </small>
                <PasswordField
                  label={t("Повтор пароля", "Repeat password")}
                  value={confirmation}
                  onChange={setConfirmation}
                  isNew
                />
                <aside
                  className="virtual-bank-notice"
                  aria-label={t(
                    "Об игровом сервисе",
                    "About this game service",
                  )}
                >
                  <b>
                    {t(
                      "Banking System — виртуальный банк",
                      "Banking System is a virtual bank",
                    )}
                  </b>
                  <p>
                    {t(
                      "Banking System — это игровой банковский сервис для Minecraft-сервера Чичи. Он не является настоящим банком или лицензированной финансовой организацией. Все счета, переводы, акции и другие финансовые инструменты существуют исключительно в рамках игровой экономики и не связаны с реальными деньгами",
                      "Banking System is a game banking service for the Chichi Minecraft server. It is not a real bank or a licensed financial institution. All accounts, transfers, shares and other financial instruments exist only within the game economy and have no connection to real money.",
                    )}
                  </p>
                  <a href="/privacy">{ui("Политика конфиденциальности")}</a>
                </aside>
              </>
            )}
            {registering && (
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={ageConfirmed}
                  onChange={(event) => setAgeConfirmed(event.target.checked)}
                />
                {ui("Подтверждаю, что мне исполнилось 15 лет")}
              </label>
            )}
            <button
              className="wide-button"
              disabled={
                busy ||
                (registering && !ageConfirmed) ||
                (registering && currentAvailability?.available === false)
              }
            >
              {busy
                ? t("Проверяем…", "Please wait…")
                : registering
                  ? t("Зарегистрироваться", "Create account")
                  : t("Войти", "Sign in")}
            </button>
          </form>
          <div className="auth-divider">{t("или", "or")}</div>
          <button
            className="wide-button secondary"
            disabled={busy || (registering && !ageConfirmed)}
            onClick={() => api.discordLogin(registering && ageConfirmed)}
          >
            {t("Продолжить через Discord", "Continue with Discord")}
          </button>
          <p className="auth-switch">
            {registering
              ? t("Уже есть аккаунт?", "Already have an account?")
              : t("Нет аккаунта?", "No account yet?")}{" "}
            <a href={registering ? "/login" : "/register"}>
              {registering
                ? t("Войти", "Sign in")
                : t("Зарегистрироваться", "Create account")}
            </a>
          </p>
          {!registering && (
            <>
              <small>
                {t(
                  "Игровой сервис сервера Чичи. Только виртуальные средства, без реальных денег.",
                  "A Chichi game service. Virtual funds only; no real money.",
                )}
              </small>
              <Disclosure title={t("Забыли пароль?", "Forgot your password?")}>
                <p>
                  {t(
                    "Если Discord был привязан, войдите через него и измените пароль в настройках безопасности. Без привязанного Discord или другого подтверждённого способа восстановления автоматический сброс недоступен. Сохраните пароль в менеджере паролей.",
                    "If Discord was linked, sign in with it and change your password in Security settings. Without a linked Discord or another verified recovery method, automatic password recovery is unavailable. Keep your password in a password manager.",
                  )}
                </p>
              </Disclosure>
            </>
          )}
        </section>
      </main>
      <footer>
        <span>Banking System · 2.6.1</span>
        <ServerLink />
        <a href="/news">{ui("Новости")}</a>
        <a href="/terms">{t("Правила пользования", "Terms of use")}</a>
        <a href="/privacy">{ui("Политика конфиденциальности")}</a>
        <a href="/api/docs">{t("Для разработчиков", "For developers")}</a>
      </footer>
    </div>
  );
}
