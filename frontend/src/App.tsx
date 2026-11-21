import { projectName } from "./branding";
import { localize as ui } from "./i18n";
import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { api, ApiFailure } from "./api";
import { bankContext, type Section, type TransferPreset } from "./bank-context";
import {
  Avatar,
  Brand,
  Icon,
  Loading,
  NewsShortcut,
  type IconName,
} from "./components/BankUI";
import { Notice } from "./components/Notice";
import { ReceiptModal } from "./components/Receipt";
import { localError, useI18n } from "./i18n";
import { Dashboard as DashboardPage } from "./pages/Dashboard";
import { SupportShortcut } from "./components/Support";
import { Transfer } from "./pages/Transfer";
import { More } from "./pages/More";
import { ThemeChoice } from "./theme";
import { Authentication, ServerLink } from "./pages/Authentication";
import type { Dashboard, Operation, RecipientReference } from "./types";
const Accounts = lazy(() =>
  import("./pages/Accounts").then((m) => ({ default: m.Accounts })),
);
const History = lazy(() =>
  import("./pages/History").then((m) => ({ default: m.History })),
);
const Organizations = lazy(() =>
  import("./pages/Organizations").then((m) => ({ default: m.Organizations })),
);
const Notifications = lazy(() =>
  import("./pages/Notifications").then((m) => ({ default: m.Notifications })),
);
const Settings = lazy(() =>
  import("./pages/Settings").then((m) => ({ default: m.Settings })),
);
const Developers = lazy(() =>
  import("./pages/Developers").then((m) => ({ default: m.Developers })),
);
const Payments = lazy(() =>
  import("./pages/Payments").then((m) => ({ default: m.Payments })),
);
const Exchange = lazy(() =>
  import("./pages/Exchange").then((m) => ({ default: m.Exchange })),
);
const Investments = lazy(() =>
  import("./pages/Investments").then((m) => ({ default: m.Investments })),
);
const Administration = lazy(() =>
  import("./pages/Administration").then((m) => ({ default: m.Administration })),
);
const sections: Section[] = [
  "home",
  "transfers",
  "payments",
  "exchange",
  "investments",
  "accounts",
  "history",
  "organizations",
  "notifications",
  "settings",
  "developers",
  "admin",
  "more",
];
function initialSection(): Section {
  const returning = sessionStorage.getItem("horizon.payment-return");
  if (
    location.pathname === "/" &&
    !location.hash &&
    returning &&
    /^\/payments(?:\/[0-9a-f-]{36})?\/?$/.test(returning)
  ) {
    history.replaceState({}, "", returning);
    sessionStorage.removeItem("horizon.payment-return");
  }
  const hash = location.hash.replace(/^#\/?/, "").split("?")[0] as Section;
  return sections.includes(hash)
    ? hash
    : location.pathname.startsWith("/payments")
      ? "payments"
      : location.pathname === "/exchange"
        ? "exchange"
        : location.pathname === "/investments"
          ? "investments"
          : "home";
}
export function App() {
  const { t, language, setLanguage } = useI18n();
  const [data, setData] = useState<Dashboard>();
  const [loading, setLoading] = useState(true);
  const [signedOut, setSignedOut] = useState(false);
  const [error, setError] = useState("");
  const [section, setSection] = useState<Section>(initialSection);
  const [receiptId, setReceiptId] = useState("");
  const [preset, setPreset] = useState<TransferPreset>();
  const [historyAccount, setHistoryAccount] = useState<string>();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [section]);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date>();
  const refresh = useCallback(async () => {
    try {
      const result = await api.dashboard();
      setData(result);
      setSignedOut(false);
      setError("");
      setLastUpdated(new Date());
    } catch (e) {
      if (e instanceof ApiFailure && e.status === 401) {
        setSignedOut(true);
        setData(undefined);
      } else {
        setError(localError(e, language));
        throw e;
      }
    }
  }, [language]);
  useEffect(() => {
    let active = true;
    refresh()
      .catch(() => undefined)
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);
  useEffect(() => {
    if (data?.profile.language) setLanguage(data.profile.language);
  }, [data?.profile.language, setLanguage]);
  useEffect(() => {
    const change = () => {
      setSection(initialSection());
    };
    window.addEventListener("hashchange", change);
    window.addEventListener("popstate", change);
    return () => {
      window.removeEventListener("hashchange", change);
      window.removeEventListener("popstate", change);
    };
  }, []);
  const navigate = useCallback((value: Section, accountId?: string) => {
    if (value === "payments") {
      history.pushState({}, "", "/payments");
    } else if (location.pathname.startsWith("/payments"))
      history.replaceState({}, "", "/");
    if (value === "history") setHistoryAccount(accountId);
    if (value === "transfers")
      setPreset({ source: accountId, mode: "external", nonce: Date.now() });
    setSection(value);
    if (value !== "payments") location.hash = "/" + value;
  }, []);
  const openReceipt = useCallback((id: string) => setReceiptId(id), []);
  const closeReceipt = useCallback(() => setReceiptId(""), []);
  const transferTo = useCallback(
    (
      recipient: RecipientReference,
      amount?: string,
      reason?: string,
      source?: string,
    ) => {
      setReceiptId("");
      navigate("transfers");
      setPreset({
        recipient,
        amount,
        reason,
        source,
        mode: "external",
        nonce: Date.now(),
      });
    },
    [navigate],
  );
  const betweenAccounts = useCallback(
    (source?: string) => {
      navigate("transfers");
      setPreset({ source, mode: "internal", nonce: Date.now() });
    },
    [navigate],
  );
  const repeatTransfer = useCallback(
    (operation: Operation) => {
      const incoming = operation.direction === "incoming";
      const owner = incoming
        ? operation.source_owner_id
        : operation.destination_owner_id;
      const account = incoming
        ? operation.source_public_number || operation.source_account_id
        : operation.destination_public_number ||
          operation.destination_account_id;
      transferTo(
        owner
          ? { kind: "user", value: owner }
          : { kind: "account", value: account },
        operation.amount.amount,
        operation.reason,
      );
    },
    [transferTo],
  );
  async function logout() {
    try {
      await api.logout();
      setData(undefined);
      setSignedOut(true);
      setReceiptId("");
    } catch (e) {
      setError(localError(e, language));
    }
  }
  async function manualRefresh() {
    setRefreshing(true);
    try {
      await refresh();
    } catch {
      /* The shared error banner retains the last real snapshot. */
    } finally {
      setRefreshing(false);
    }
  }
  if (loading && !data)
    return (
      <div className="startup">
        <Brand />
        <Loading />
      </div>
    );
  if (signedOut)
    return <Authentication error={error} onAuthenticated={refresh} />;
  if (!data)
    return (
      <div className="startup">
        <Brand />
        <h1>{t("Не удалось загрузить банк", "Could not load your bank")}</h1>
        <Notice error={error} />
        <button disabled={refreshing} onClick={() => void manualRefresh()}>
          {t("Попробовать снова", "Try again")}
        </button>
        <button className="text-button" onClick={() => setSignedOut(true)}>
          {t("Перейти ко входу", "Go to sign in")}
        </button>
      </div>
    );
  const nav: {
    id: Section;
    icon: IconName;
    ru: string;
    en: string;
  }[] = [
    { id: "home", icon: "home", ru: "Главная", en: "Home" },
    { id: "transfers", icon: "transfer", ru: "Переводы", en: "Transfers" },
    { id: "payments", icon: "wallet", ru: "Платежи", en: "Payments" },
    { id: "exchange", icon: "chart", ru: "Биржа", en: "Exchange" },
    { id: "investments", icon: "wallet", ru: "Инвестиции", en: "Investments" },
    { id: "accounts", icon: "wallet", ru: "Счета", en: "Accounts" },
    { id: "history", icon: "history", ru: "История", en: "Activity" },
    {
      id: "organizations",
      icon: "organization",
      ru: "Организации",
      en: "Organizations",
    },
    {
      id: "notifications",
      icon: "bell",
      ru: "Уведомления",
      en: "Notifications",
    },
    { id: "settings", icon: "settings", ru: "Настройки", en: "Settings" },
    { id: "more", icon: "menu", ru: "Ещё", en: "More" },
  ];
  const active =
    section === "admin" && !data.profile.permissions.length ? "home" : section;
  return (
    <bankContext.Provider
      value={{
        data,
        refresh,
        navigate,
        openReceipt,
        transferTo,
        repeatTransfer,
        transferPreset: preset,
        historyAccount,
        betweenAccounts,
      }}
    >
      <div className="app-shell">
        <a
          className="skip-link"
          href="#main-content"
          onClick={(event) => {
            event.preventDefault();
            document.getElementById("main-content")?.focus();
          }}
        >
          {t("К содержимому", "Skip to content")}
        </a>
        <aside className="sidebar">
          <div className="sidebar-brand">
            <Brand />
          </div>
          <p className="sidebar-label">
            {t("ЛИЧНЫЙ КАБИНЕТ", "PERSONAL BANKING")}
          </p>
          <nav aria-label={t("Основная навигация", "Main navigation")}>
            {(
              [
                "home",
                "accounts",
                "transfers",
                "exchange",
                "organizations",
                "settings",
                "more",
              ] as Section[]
            )
              .map((id) => nav.find((item) => item.id === id)!)
              .map((item) => (
                <button
                  key={item.id}
                  aria-current={active === item.id ? "page" : undefined}
                  className={active === item.id ? "active" : ""}
                  onClick={() => navigate(item.id)}
                >
                  <Icon name={item.icon} />
                  <span>{t(item.ru, item.en)}</span>
                  {item.id === "notifications" &&
                    data.unread_notifications > 0 && (
                      <span className="nav-count">
                        {data.unread_notifications}
                      </span>
                    )}
                </button>
              ))}
          </nav>
          <div className="sidebar-bottom">
            <a className="sidebar-terms" href="/terms">
              {ui("Правила пользования")}
            </a>
            <button
              className={active === "developers" ? "active" : ""}
              onClick={() => navigate("developers")}
            >
              <Icon name="code" />
              {t("Для разработчиков", "For developers")}
            </button>
            {data.profile.permissions.length > 0 && (
              <button
                className={active === "admin" ? "active" : ""}
                onClick={() => navigate("admin")}
              >
                <Icon name="shield" />
                {t("Администрирование", "Administration")}
              </button>
            )}
            <small className="sidebar-version">{projectName} 2.6.1</small>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <span className="mobile-brand">
              <Brand />
            </span>
            <div className="topbar-path">
              {t("Личный кабинет", "Personal banking")}
              <span>/</span>
              <b>
                {nav.find((item) => item.id === active)
                  ? t(
                      nav.find((item) => item.id === active)!.ru,
                      nav.find((item) => item.id === active)!.en,
                    )
                  : active === "developers"
                    ? t("Для разработчиков", "For developers")
                    : t("Администрирование", "Administration")}
              </b>
            </div>
            <div className="topbar-actions">
              <ThemeChoice compact />
              <button
                className="icon-button refresh-button"
                disabled={refreshing}
                aria-label={t("Обновить данные", "Refresh data")}
                onClick={() => void manualRefresh()}
              >
                <Icon name="refresh" size={18} />
              </button>
              <NewsShortcut />
              <SupportShortcut />
              <button
                className="icon-button bell-button"
                aria-label={t("Уведомления", "Notifications")}
                onClick={() => navigate("notifications")}
              >
                <Icon name="bell" />
                {data.unread_notifications > 0 && <span />}
              </button>
              <button
                className="user-menu"
                aria-label={
                  t("Открыть профиль", "Open profile") +
                  ": " +
                  data.profile.display_name
                }
                onClick={() => navigate("settings")}
              >
                <Avatar name={data.profile.display_name || "H"} />
                <span>
                  <b>{data.profile.display_name}</b>
                  <small>
                    {data.profile.bank_username
                      ? "@" + data.profile.bank_username
                      : projectName}
                  </small>
                </span>
                <Icon name="chevron" size={15} />
              </button>
              <button
                className="icon-button logout-button"
                aria-label={t("Выйти", "Sign out")}
                onClick={() => void logout()}
              >
                <Icon name="logout" size={18} />
              </button>
            </div>
          </header>
          <main id="main-content" className="main-content" tabIndex={-1}>
            <Notice error={error} />
            <div key={active} className="page-transition">
              <Suspense fallback={<Loading />}>
                {active === "home" && <DashboardPage />}
                {active === "transfers" && <Transfer />}
                {active === "payments" && (
                  <Suspense fallback={<Loading />}>
                    <Payments />
                  </Suspense>
                )}
                {active === "accounts" && <Accounts />}
                {active === "exchange" && (
                  <Suspense fallback={<Loading />}>
                    <Exchange />
                  </Suspense>
                )}
                {active === "investments" && (
                  <Suspense fallback={<Loading />}>
                    <Investments />
                  </Suspense>
                )}
                {active === "history" && <History />}
                {active === "organizations" && <Organizations />}
                {active === "notifications" && <Notifications />}
                {active === "settings" && <Settings />}
                {active === "developers" && <Developers />}
                {active === "more" && <More onLogout={logout} />}
                {active === "admin" && (
                  <Suspense fallback={<Loading />}>
                    <Administration permissions={data.profile.permissions} />
                  </Suspense>
                )}
              </Suspense>
            </div>
            <footer className="app-footer">
              <ServerLink />
              <a href="/terms">{ui("Правила пользования")}</a>
              <a href="/privacy">{ui("Политика конфиденциальности")}</a>
              <span>
                © {projectName} ·{" "}
                {t("Ваш мир. Ваш банк.", "Your world. Your bank.")}
              </span>
              <span>
                <span className={"status-dot" + (error ? " warning" : "")} />
                {error
                  ? t("Проверьте соединение", "Check your connection")
                  : t("Обновлено ", "Updated ") +
                    (lastUpdated
                      ? new Intl.DateTimeFormat(
                          language === "uk-UA"
                            ? "uk-UA"
                            : language === "ru"
                              ? "ru-RU"
                              : "en-GB",
                          { hour: "2-digit", minute: "2-digit" },
                        ).format(lastUpdated)
                      : "—")}
              </span>
            </footer>
          </main>
        </div>
        <nav
          className="bottom-nav"
          aria-label={t("Мобильная навигация", "Mobile navigation")}
        >
          {(
            ["home", "accounts", "transfers", "exchange", "more"] as Section[]
          ).map((id) => {
            const item = nav.find((item) => item.id === id)!;
            const current =
              active === id ||
              (id === "more" &&
                !["home", "accounts", "transfers", "exchange"].includes(
                  active,
                ));
            return (
              <button
                key={id}
                aria-current={current ? "page" : undefined}
                onClick={() => navigate(id)}
              >
                <Icon name={item.icon} />
                <span>{t(item.ru, item.en)}</span>
              </button>
            );
          })}
        </nav>
        {receiptId && (
          <ReceiptModal
            id={receiptId}
            onClose={closeReceipt}
            onRepeat={repeatTransfer}
          />
        )}
      </div>
    </bankContext.Provider>
  );
}
