import { useEffect, useState } from "react";
import { api } from "../api";
import { PendingPayments } from "../components/PendingPayments";
import { useBank } from "../bank-context";
import {
  AccountCard,
  Empty,
  Icon,
  Loading,
  Operations,
  PageHeading,
} from "../components/BankUI";
import { Notice } from "../components/Notice";
import { currencyLabel, money, useI18n } from "../i18n";
import { exchangeApi, type Page, type Position } from "../exchange-api";
import type { Organization } from "../types";
export function Dashboard() {
  const { data, navigate, openReceipt, betweenAccounts } = useBank();
  const { t, language } = useI18n();
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem("horizon.hideAmounts") === "true";
    } catch {
      return false;
    }
  });
  const [organizations, setOrganizations] = useState<Organization[]>(),
    [positions, setPositions] = useState<Page<Position>>(),
    [error, setError] = useState("");
  const name =
    data.profile.display_name ||
    data.profile.bank_username ||
    t("друг", "friend");
  useEffect(() => {
    let active = true;
    Promise.allSettled([
      api.organizations(),
      exchangeApi.get<Page<Position>>("portfolio", {
        account_id: data.primary_account.id,
        limit: "3",
      }),
    ]).then(([orgs, portfolio]) => {
      if (!active) return;
      if (orgs.status === "fulfilled") setOrganizations(orgs.value);
      if (portfolio.status === "fulfilled") setPositions(portfolio.value);
      if (orgs.status === "rejected" || portfolio.status === "rejected")
        setError(
          t(
            "Часть обзора недоступна. Счета и последние операции показаны по последнему ответу банка.",
            "Part of your overview is unavailable. Accounts and recent activity use the latest bank snapshot.",
          ),
        );
    });
    return () => {
      active = false;
    };
  }, [data.primary_account.id, language]);
  const show = (amount: string) =>
    hidden ? "•••• " + currencyLabel(language) : money(amount, language);
  function toggle() {
    const value = !hidden;
    setHidden(value);
    try {
      localStorage.setItem("horizon.hideAmounts", String(value));
    } catch {
      /* Privacy still applies in this tab. */
    }
  }
  return (
    <>
      <PageHeading
        title={t(
          `Рады видеть вас, ${name}`,
          `Good to see you, ${name}`,
          `Раді бачити вас, ${name}`,
        )}
        subtitle={t(
          "Ваши деньги, планы и последние события",
          "Your money, plans and latest activity",
        )}
      />
      <div className="dashboard-top">
        <section className="balance-hero">
          <div className="hero-top">
            <span className="eyebrow">
              {t("ОСНОВНОЙ ЛИЧНЫЙ СЧЁТ", "PRIMARY PERSONAL ACCOUNT")}
            </span>
            <button
              className="icon-button"
              onClick={toggle}
              aria-label={
                hidden
                  ? t("Показать суммы", "Show amounts")
                  : t("Скрыть суммы", "Hide amounts")
              }
              aria-pressed={hidden}
            >
              <Icon name="eye" />
            </button>
          </div>
          <div className="hero-amount">
            {hidden
              ? "••••"
              : money(
                  data.primary_account.available.amount,
                  language,
                  false,
                )}{" "}
            <span>{currencyLabel(language)}</span>
          </div>
          <p>
            {data.primary_account.name} ·{" "}
            {data.primary_account.public_number.slice(-4)}
          </p>
          <div className="quick-actions">
            <button onClick={() => navigate("transfers")}>
              <span>
                <Icon name="outgoing" />
              </span>
              {t("Перевести", "Send")}
            </button>
            <button onClick={() => betweenAccounts()}>
              <span>
                <Icon name="swap" />
              </span>
              {t("Между счетами", "Between accounts")}
            </button>
            <button onClick={() => navigate("history")}>
              <span>
                <Icon name="history" />
              </span>
              {t("История", "Activity")}
            </button>
            <button onClick={() => navigate("accounts")}>
              <span>
                <Icon name="wallet" />
              </span>
              {t("Все счета", "All accounts")}
            </button>
          </div>
        </section>
        <section className="primary-summary">
          <div className="section-heading">
            <h2>{t("Личные финансы", "Personal finances")}</h2>
            <span className="tag">{currencyLabel(language)}</span>
          </div>
          <dl className="key-values">
            <div>
              <dt>{t("Всего доступно", "Total available")}</dt>
              <dd>{show(data.total_available.amount)}</dd>
            </div>
            <div>
              <dt>{t("Резерв основного счёта", "Primary account reserved")}</dt>
              <dd>{show(data.primary_account.held.amount)}</dd>
            </div>
            <div>
              <dt>{t("Ваши личные счета", "Your personal accounts")}</dt>
              <dd>
                {
                  data.accounts.filter(
                    (a) =>
                      a.owner_id === data.profile.id &&
                      a.account_type === "personal",
                  ).length
                }
              </dd>
            </div>
          </dl>
          <small>
            {t(
              "Деньги организаций учитываются отдельно. Зарезервированные средства временно недоступны для перевода.",
              "Organization funds are separate. Reserved funds are temporarily unavailable for transfers.",
            )}
          </small>
          <button className="text-button" onClick={() => navigate("accounts")}>
            {t("Управлять счетами", "Manage accounts")}
            <Icon name="arrow" size={17} />
          </button>
        </section>
      </div>
      <PendingPayments />
      <div className="section-heading spaced">
        <h2>{t("Мои счета", "My accounts")}</h2>
        <button className="text-button" onClick={() => navigate("accounts")}>
          {t("Посмотреть все", "View all")}
          <Icon name="chevron" size={16} />
        </button>
      </div>
      <div className="account-grid">
        {data.accounts
          .filter(
            (a) =>
              a.owner_id === data.profile.id && a.account_type === "personal",
          )
          .slice(0, 3)
          .map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              concealed={hidden}
              onClick={() => navigate("accounts")}
            />
          ))}
      </div>
      <section className="panel activity-panel">
        <div className="section-heading">
          <h2>{t("Последние операции", "Recent activity")}</h2>
          <button className="text-button" onClick={() => navigate("history")}>
            {t("Вся история", "All activity")}
            <Icon name="arrow" size={17} />
          </button>
        </div>
        {data.recent_operations.length ? (
          <Operations
            items={data.recent_operations.slice(0, 5)}
            onOpen={openReceipt}
            concealed={hidden}
          />
        ) : (
          <Empty
            icon="history"
            title={t("Здесь начнётся ваша история", "Your story starts here")}
            description={t(
              "Отправьте первый перевод — его квитанция появится здесь.",
              "Send your first transfer and its receipt will appear here.",
            )}
            action={
              <button
                className="secondary"
                onClick={() => navigate("transfers")}
              >
                {t("Сделать перевод", "Make a transfer")}
              </button>
            }
          />
        )}
      </section>
      <div className="dashboard-links">
        <section className="panel">
          <div className="section-heading">
            <h2>{t("Инвестиции", "Investments")}</h2>
            <Icon name="chart" />
          </div>
          {!positions ? (
            error ? (
              <p className="muted">
                {t("Портфель пока недоступен", "Portfolio unavailable")}
              </p>
            ) : (
              <Loading />
            )
          ) : positions.items.length ? (
            <>
              <small>
                {t(
                  "Акции на основном личном счёте. Оценка по последним сделкам.",
                  "Holdings in your primary personal account. Values use the latest trades.",
                )}
              </small>
              <div className="compact-list">
                {positions.items.map((p) => (
                  <button
                    key={p.stock_id}
                    onClick={() => navigate("investments")}
                  >
                    <span>
                      {p.ticker} · {p.quantity} {t("акций", "shares")}
                    </span>
                    <b>
                      {p.estimated_value
                        ? show(p.estimated_value)
                        : t("Нет оценки", "No valuation")}
                    </b>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">
              {t(
                "На основном счёте пока нет акций. Изучите компании на бирже или откройте портфели других счетов.",
                "No shares in your primary account yet. Explore the market or open your other portfolios.",
              )}
            </p>
          )}
          <button
            className="text-button"
            onClick={() => navigate("investments")}
          >
            {t("Портфель и заявки", "Portfolio and orders")}
            <Icon name="arrow" size={17} />
          </button>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>{t("Организации", "Organizations")}</h2>
            <Icon name="organization" />
          </div>
          {!organizations ? (
            error ? (
              <p className="muted">
                {t(
                  "Список организаций пока недоступен",
                  "Organizations unavailable",
                )}
              </p>
            ) : (
              <Loading />
            )
          ) : organizations.length ? (
            <div className="compact-list">
              {organizations.slice(0, 3).map((org) => (
                <button
                  key={org.id}
                  onClick={() => {
                    navigate("organizations");
                    location.hash = "/organizations?organization_id=" + org.id;
                  }}
                >
                  <span>{org.name}</span>
                  <Icon name="chevron" size={16} />
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">
              {t(
                "Счета, сотрудники, зарплаты и аналитика для вашего общего дела.",
                "Accounts, people, payroll and insights for your venture.",
              )}
            </p>
          )}
          <button
            className="text-button"
            onClick={() => navigate("organizations")}
          >
            {t("Открыть организации", "Open organizations")}
            <Icon name="arrow" size={17} />
          </button>
        </section>
      </div>
      {error && <Notice error={error} />}
    </>
  );
}
