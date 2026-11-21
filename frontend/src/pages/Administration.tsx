import { localize as ui, useI18n as useLocale } from "../i18n";
import { useEffect, useState } from "react";
import { adminApi, type AdminAccount, type AdminUser } from "../admin-api";
import {
  Icon,
  Loading,
  PageHeading,
  type IconName,
} from "../components/BankUI";
import { Notice } from "../components/Notice";
import { dateTime, localError } from "../i18n";
import { Users } from "./admin/Users";
import { Roles } from "./admin/Roles";
import { TermsManager } from "./admin/TermsManager";
import { Accounts, Finances, Transactions } from "./admin/Finance";
import { Audit, Investigations } from "./admin/Audit";
import { ExchangeAdmin } from "./admin/ExchangeAdmin";
import { NewsManager } from "./admin/NewsManager";
import { AtmManager } from "./admin/AtmManager";
import { PrivacyManager } from "./admin/PrivacyManager";
import { SupportPanel } from "../components/Support";
const sections = (): {
  id: string;
  name: string;
  icon: IconName;
  permissions: string[];
}[] => [
  {
    id: "privacy",
    name: ui("Персональные данные"),
    icon: "shield",
    permissions: ["admin.privacy.read"],
  },
  {
    id: "support",
    name: ui("Поддержка"),
    icon: "headset",
    permissions: ["admin.support.read"],
  },
  {
    id: "exchange",
    name: ui("Биржа"),
    icon: "chart",
    permissions: ["admin.exchange.read"],
  },
  {
    id: "overview",
    name: ui("Обзор"),
    icon: "home",
    permissions: ["admin.overview"],
  },
  {
    id: "users",
    name: ui("Пользователи"),
    icon: "user",
    permissions: ["admin.users.read"],
  },
  {
    id: "accounts",
    name: ui("Счета"),
    icon: "wallet",
    permissions: ["admin.accounts.read"],
  },
  {
    id: "transactions",
    name: ui("Транзакции"),
    icon: "transfer",
    permissions: ["admin.transactions.read"],
  },
  {
    id: "finances",
    name: ui("Финансы"),
    icon: "wallet",
    permissions: ["admin.issuance", "admin.refunds", "admin.finance.manage"],
  },
  {
    id: "roles",
    name: ui("Роли и разрешения"),
    icon: "shield",
    permissions: ["admin.roles.manage"],
  },
  {
    id: "terms",
    name: ui("Правила банка"),
    icon: "info",
    permissions: ["admin.terms.manage"],
  },
  {
    id: "news",
    name: ui("Управление новостями"),
    icon: "info",
    permissions: ["admin.news.manage"],
  },
  {
    id: "atm",
    name: ui("Банкоматы"),
    icon: "wallet",
    permissions: ["admin.atm.read"],
  },
  {
    id: "investigations",
    name: ui("Расследования"),
    icon: "search",
    permissions: ["admin.investigations.read"],
  },
  {
    id: "audit",
    name: ui("Аудит"),
    icon: "history",
    permissions: ["admin.audit"],
  },
];
type OverviewData = {
  users: number;
  accounts?: number;
  operations?: number;
  core: string;
  recent_actions: {
    action: string;
    created_at: string;
  }[];
};
function Overview() {
  useLocale();
  const [data, setData] = useState<OverviewData>(),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    adminApi
      .get<OverviewData>("overview")
      .then((x) => {
        if (active) setData(x);
      })
      .catch((e) => {
        if (active) setError(localError(e));
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <>
      <Notice error={error} />
      {!data && !error ? (
        <Loading />
      ) : (
        data && (
          <>
            <div className="admin-stat-grid">
              <div className="panel">
                <span>{ui("Пользователи")}</span>
                <h2>{data.users}</h2>
              </div>
              <div className="panel">
                <span>{ui("Банковские счета")}</span>
                <h2>{data.accounts ?? "—"}</h2>
              </div>
              <div className="panel">
                <span>{ui("Транзакции")}</span>
                <h2>{data.operations ?? "—"}</h2>
              </div>
            </div>
            <section className="panel">
              <h3>{ui("Состояние финансового сервиса")}</h3>
              <p>
                {data.core === "ready"
                  ? ui("Ядро доступно")
                  : data.core === "permission_required"
                    ? ui(
                        "Для финансового обзора требуется отдельное полномочие admin.read в ядре",
                      )
                    : ui("Ядро временно недоступно")}
              </p>
            </section>
            <h3>{ui("Ваши последние административные действия")}</h3>
            {data.recent_actions.map((a, i) => (
              <div className="delivery-row" key={i}>
                <b>{a.action}</b>
                <small>{dateTime(a.created_at, undefined)}</small>
              </div>
            ))}
          </>
        )
      )}
    </>
  );
}
export function Administration({ permissions }: { permissions: string[] }) {
  useLocale();
  const available = sections().filter((x) =>
    x.permissions.some((p) => permissions.includes(p)),
  );
  const [section, setSection] = useState(available[0]?.id || ""),
    [owner, setOwner] = useState<AdminUser>(),
    [account, setAccount] = useState<AdminAccount>();
  const active = available.some((x) => x.id === section)
    ? section
    : available[0]?.id;
  return (
    <>
      <PageHeading
        title={ui("Административный кабинет")}
        subtitle={ui("Управление банком, доступом и публикациями")}
      />
      <label className="admin-mobile-select">
        {ui("Раздел администрации")}
        <select
          value={active || ""}
          onChange={(e) => {
            setSection(e.target.value);
            setOwner(undefined);
            setAccount(undefined);
          }}
        >
          {available.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <div className="admin-layout">
        <nav className="admin-nav" aria-label={ui("Разделы администрации")}>
          {available.map((item) => (
            <button
              key={item.id}
              className={active === item.id ? "active" : ""}
              aria-current={active === item.id ? "page" : undefined}
              onClick={() => {
                setSection(item.id);
                setOwner(undefined);
                setAccount(undefined);
              }}
            >
              <Icon name={item.icon} size={18} />
              {item.name}
            </button>
          ))}
        </nav>
        <section className="admin-content">
          <h2>{available.find((x) => x.id === active)?.name}</h2>
          {active === "overview" && <Overview />}
          {active === "privacy" && <PrivacyManager permissions={permissions} />}
          {active === "exchange" && <ExchangeAdmin permissions={permissions} />}
          {active === "users" && (
            <Users
              permissions={permissions}
              onAccounts={(u) => {
                setOwner(u);
                setSection("accounts");
              }}
            />
          )}
          {active === "accounts" && (
            <Accounts
              key={owner?.id || "all"}
              permissions={permissions}
              owner={owner}
              onHistory={(a) => {
                setAccount(a);
                setSection("transactions");
              }}
            />
          )}
          {active === "transactions" && (
            <Transactions
              key={account?.id || "all"}
              permissions={permissions}
              account={account}
            />
          )}
          {active === "finances" && <Finances permissions={permissions} />}
          {active === "roles" && <Roles permissions={permissions} />}
          {active === "terms" && <TermsManager />}
          {active === "news" && <NewsManager />}
          {active === "atm" && <AtmManager permissions={permissions} />}
          {active === "support" && (
            <SupportPanel admin permissions={permissions} />
          )}
          {active === "audit" && <Audit />}
          {active === "investigations" && (
            <Investigations
              editable={permissions.includes("admin.investigations")}
            />
          )}
          {!active && <p>{ui("У вас нет административных разрешений.")}</p>}
        </section>
      </div>
    </>
  );
}
