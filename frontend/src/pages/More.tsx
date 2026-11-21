import { localize as ui } from "../i18n";
import { useBank, type Section } from "../bank-context";
import { PageHeading, Icon, type IconName } from "../components/BankUI";
import { ThemeChoice } from "../theme";
import { useI18n } from "../i18n";
export function More({ onLogout }: { onLogout: () => Promise<void> }) {
  const { data, navigate } = useBank();
  const { t } = useI18n();
  const items: [Section, IconName, string, string, string, string][] = [
    [
      "organizations",
      "organization",
      ui("Организации"),
      "Organizations",
      ui("Счета бизнеса, сотрудники, зарплаты и аналитика"),
      "Business accounts, people, payroll and insights",
    ],
    [
      "investments",
      "chart",
      ui("Инвестиции"),
      "Investments",
      ui("Ваш портфель, заявки, сделки и дивиденды"),
      "Your portfolio, orders, trades and dividends",
    ],
    [
      "payments",
      "wallet",
      ui("Платежи"),
      "Payments",
      ui("Запросы на оплату и платежи магазинам"),
      "Payment requests and merchant payments",
    ],
    [
      "history",
      "history",
      ui("История"),
      "Activity",
      ui("Все операции и квитанции"),
      "All transactions and receipts",
    ],
    [
      "notifications",
      "bell",
      ui("Уведомления"),
      "Notifications",
      ui("События по счетам и сообщения банка"),
      "Account activity and bank messages",
    ],
    [
      "settings",
      "settings",
      ui("Настройки"),
      "Settings",
      ui("Профиль, оформление, безопасность и уведомления"),
      "Profile, appearance, security and notifications",
    ],
    [
      "developers",
      "code",
      ui("Для разработчиков"),
      "Developers",
      ui("API-ключи и документация"),
      "API keys and documentation",
    ],
    ...(data.profile.permissions.length
      ? [
          [
            "admin",
            "shield",
            ui("Администрирование"),
            "Administration",
            ui("Управление банком в рамках ваших разрешений"),
            "Bank management within your permissions",
          ] as [Section, IconName, string, string, string, string],
        ]
      : []),
  ];
  return (
    <>
      <PageHeading
        title={t("Ещё", "More")}
        subtitle={t(
          "Всё остальное — в одном месте",
          "Everything else, in one place",
        )}
      />
      <div className="more-grid">
        <a className="more-card" href="/news">
          <span className="account-icon">
            <Icon name="newspaper" />
          </span>
          <span>
            <b>{t("Новости", "News")}</b>
            <small>
              {t(
                "Официальные объявления и обновления Banking System",
                "Official announcements and updates from Banking System",
              )}
            </small>
          </span>
          <Icon name="chevron" />
        </a>
        {items.map(([id, icon, ru, en, descRu, descEn]) => (
          <button key={id} className="more-card" onClick={() => navigate(id)}>
            <span className="account-icon">
              <Icon name={icon} />
            </span>
            <span>
              <b>{t(ru, en)}</b>
              <small>{t(descRu, descEn)}</small>
            </span>
            <Icon name="chevron" />
          </button>
        ))}
      </div>
      <section className="panel appearance-panel">
        <ThemeChoice />
      </section>
      <button className="secondary danger" onClick={() => void onLogout()}>
        <Icon name="logout" />
        {t("Выйти из аккаунта", "Sign out")}
      </button>
    </>
  );
}
