import { localize as ui } from "../i18n";
import {
  Suspense,
  lazy,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { api } from "../api";
import { useBank } from "../bank-context";
import {
  Avatar,
  Empty,
  Icon,
  Loading,
  Modal,
  PageHeading,
} from "../components/BankUI";
import { Notice } from "../components/Notice";
import { RecipientPicker } from "../components/RecipientPicker";
import { Disclosure, tabKeys } from "../components/Flow";
import { cents, decimal } from "../money";
import { localError, money, useI18n } from "../i18n";
import type { Organization, OrganizationMember, Recipient } from "../types";
import { HPay } from "./business/HPay";
const BusinessSections = lazy(() =>
  import("./business/BusinessSections").then((m) => ({
    default: m.BusinessSections,
  })),
);
export function Organizations() {
  const { data, refresh, navigate, betweenAccounts } = useBank();
  const { t, language } = useI18n();
  const [items, setItems] = useState<Organization[]>([]);
  const [selected, setSelected] = useState<Organization>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [accountName, setAccountName] = useState("");
  const accountKey = useRef(crypto.randomUUID());
  const [memberOpen, setMemberOpen] = useState(false);
  const [recipient, setRecipient] = useState<Recipient>();
  const [role, setRole] = useState<"member" | "administrator">("member");
  const [grants, setGrants] = useState([
    "accounts.read",
    "balance.read",
    "history.read",
  ]);
  const [removing, setRemoving] = useState<OrganizationMember>();
  const [orgTab, setOrgTab] = useState("overview");
  const selecting = useRef(0);
  useEffect(() => {
    api
      .organizations()
      .then((organizations) => {
        setItems(organizations);
        const id = new URLSearchParams(location.hash.split("?")[1]).get(
          "organization_id",
        );
        if (id && organizations.some((org) => org.id === id)) void select(id);
      })
      .catch((e) => setError(localError(e, language)))
      .finally(() => setLoading(false));
  }, [language]);
  useEffect(() => {
    const change = () => {
      const id = new URLSearchParams(location.hash.split("?")[1]).get(
        "organization_id",
      );
      if (id && id !== selected?.id && items.some((o) => o.id === id))
        void select(id);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, [items, selected?.id]);
  async function select(id: string) {
    const n = ++selecting.current;
    setBusy(true);
    setError("");
    try {
      const value = await api.organization(id);
      if (n === selecting.current) {
        setSelected(value);
        const requested = new URLSearchParams(location.hash.split("?")[1]).get(
          "tab",
        );
        setOrgTab(
          requested &&
            [
              "overview",
              "accounts",
              "members",
              "payments",
              "payroll",
              "analytics",
              "exchange",
            ].includes(requested)
            ? requested
            : "overview",
        );
      }
    } catch (e) {
      if (n === selecting.current) setError(localError(e, language));
    } finally {
      if (n === selecting.current) setBusy(false);
    }
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const org = await api.createOrganization(name);
      setItems(await api.organizations());
      setSelected(await api.organization(org.id));
      setCreateOpen(false);
      setName("");
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function addAccount(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await api.organizationAccount(
        selected.id,
        accountName,
        accountKey.current,
      );
      setSelected(await api.organization(selected.id));
      await refresh();
      setAccountOpen(false);
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function addMember(event: FormEvent) {
    event.preventDefault();
    if (!selected || !recipient?.id) return;
    setBusy(true);
    setError("");
    try {
      setSelected(
        await api.organizationMember(selected.id, recipient.id, role, grants),
      );
      setMemberOpen(false);
      await refresh();
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  async function removeMember() {
    if (!selected || !removing) return;
    setBusy(true);
    setError("");
    try {
      await api.removeOrganizationMember(selected.id, removing.user_id);
      setSelected(await api.organization(selected.id));
      setRemoving(undefined);
      await refresh();
    } catch (e) {
      setError(localError(e, language));
    } finally {
      setBusy(false);
    }
  }
  const manager =
    selected?.current_role === "owner" ||
    selected?.current_role === "administrator";
  const orgPermissions =
    selected?.members?.find((member) => member.user_id === data.profile.id)
      ?.permissions || [];
  const orgTabs = {
    overview: t("Обзор", "Overview"),
    accounts: t("Счета", "Accounts"),
    members: t("Участники", "Members"),
    payments: t("Платежи", "Payments"),
    ...(manager ? { hpay: "HPay" } : {}),
    ...(orgPermissions.includes("payroll.read")
      ? { payroll: t("Зарплаты", "Payroll") }
      : {}),
    ...(orgPermissions.includes("analytics.read")
      ? { analytics: t("Аналитика", "Insights") }
      : {}),
    ...(orgPermissions.some(
      (p) =>
        p === "exchange.issuer.manage" || p === "exchange.dividends.manage",
    )
      ? { exchange: t("Акции", "Shares") }
      : {}),
  };
  const permissionNames: [string, string, string][] = [
    ["accounts.read", ui("Просмотр счетов"), "View accounts"],
    ["balance.read", ui("Просмотр баланса"), "View balance"],
    ["history.read", ui("История"), "Activity"],
    ["transfer", ui("Переводы"), "Transfers"],
    ["payment.create", ui("Платежи"), "Payments"],
    ["hold.create", ui("Блокировка средств"), "Reserve funds"],
    [
      "payment_requests.create",
      ui("Выставлять запросы на оплату"),
      "Create payment requests",
    ],
    [
      "payment_requests.read",
      ui("Просмотр запросов и оплат"),
      "Read payment requests",
    ],
    [
      "payment_requests.cancel",
      ui("Отмена запросов"),
      "Cancel payment requests",
    ],
    ["analytics.read", ui("Просмотр аналитики"), "Read analytics"],
    ["exchange.trade", ui("Торговля акциями"), "Trade securities"],
    [
      "exchange.issuer.manage",
      ui("Управление выпуском акций"),
      "Manage issues",
    ],
    [
      "exchange.dividends.manage",
      ui("Управление дивидендами"),
      "Manage dividends",
    ],
    ["analytics.export", ui("Экспорт аналитики"), "Export analytics"],
    [
      "analytics.categorize",
      ui("Категоризация операций"),
      "Categorize operations",
    ],
    ["payroll.read", ui("Просмотр зарплат"), "Read payroll"],
    [
      "payroll.employees.manage",
      ui("Управление сотрудниками"),
      "Manage employees",
    ],
    ["payroll.salaries.manage", ui("Изменение зарплат"), "Manage salaries"],
    [
      "payroll.batches.create",
      ui("Создание ведомостей"),
      "Create payroll batches",
    ],
    ["payroll.approve", ui("Утверждение ведомостей"), "Approve payroll"],
    ["payroll.execute", ui("Выполнение выплат"), "Execute payroll"],
    [
      "payroll.automatic.manage",
      ui("Управление расписаниями"),
      "Manage payroll schedules",
    ],
  ];
  return (
    <>
      <PageHeading
        title={t("Организации", "Organizations")}
        subtitle={t(
          "Общие планы, отдельные счета и понятные права",
          "Shared plans, separate accounts and clear permissions",
        )}
        action={
          <button
            onClick={() => {
              setName("");
              setCreateOpen(true);
            }}
          >
            <Icon name="plus" />
            {t("Создать организацию", "Create organization")}
          </button>
        }
      />
      <Notice error={error} />
      {loading ? (
        <Loading />
      ) : !items.length ? (
        <section className="panel">
          <Empty
            icon="organization"
            title={t("Объединяйтесь для большего", "Build something together")}
            description={t(
              "Создайте организацию для магазина, команды или общего проекта. Личные средства останутся отдельно.",
              "Create an organization for a shop, team or shared project. Personal funds stay separate.",
            )}
          />
        </section>
      ) : (
        <div className="organizations-layout">
          <section className="panel organization-list">
            {items.map((org) => (
              <button
                key={org.id}
                className={selected?.id === org.id ? "selected" : ""}
                onClick={() => void select(org.id)}
              >
                <span className="organization-mark">
                  <Icon name="organization" />
                </span>
                <b>{org.name}</b>
                <Icon name="chevron" size={16} />
              </button>
            ))}
          </section>
          {selected ? (
            <div className="organization-content">
              <div
                className="business-section-tabs"
                role="tablist"
                onKeyDown={tabKeys}
                aria-label={t("Разделы организации", "Organization sections")}
              >
                {Object.entries(orgTabs).map(([id, title]) => (
                  <button
                    key={id}
                    role="tab"
                    aria-selected={orgTab === id}
                    className={orgTab === id ? "" : "secondary"}
                    onClick={() => setOrgTab(id)}
                  >
                    {title}
                  </button>
                ))}
              </div>
              {orgTab === "overview" && (
                <section className="panel organization-overview">
                  <div className="org-hero">
                    <span className="account-icon">
                      <Icon name="organization" />
                    </span>
                    <div>
                      <h2>{selected.name}</h2>
                      <small>
                        {t(
                          "Ваше рабочее пространство",
                          "Your business workspace",
                        )}
                      </small>
                    </div>
                    <span className="tag">
                      {selected.current_role === "owner"
                        ? t("Владелец", "Owner")
                        : manager
                          ? t("Администратор", "Administrator")
                          : t("Участник", "Member")}
                    </span>
                  </div>
                  <div className="business-metrics">
                    <div>
                      <span>
                        {t("На доступных счетах", "Accessible account funds")}
                      </span>
                      <h2>
                        {money(
                          decimal(
                            (selected.accounts || []).reduce(
                              (sum, account) =>
                                sum + cents(account.available.amount),
                              0n,
                            ),
                          ),
                          language,
                        )}
                      </h2>
                      <small>
                        {t(
                          "Только счета, доступные вам для просмотра",
                          "Only accounts you can view",
                        )}
                      </small>
                    </div>
                    <div>
                      <span>{t("Участников", "Members")}</span>
                      <h2>{selected.members?.length ?? "—"}</h2>
                    </div>
                  </div>
                  <div className="button-row">
                    <button
                      className="secondary"
                      onClick={() => setOrgTab("members")}
                    >
                      <Icon name="user" />
                      {t("Команда", "Team")}
                    </button>
                    {orgPermissions.includes("payroll.read") && (
                      <button
                        className="secondary"
                        onClick={() => setOrgTab("payroll")}
                      >
                        {t("Зарплаты", "Payroll")}
                      </button>
                    )}
                    {orgPermissions.includes("analytics.read") && (
                      <button
                        className="secondary"
                        onClick={() => setOrgTab("analytics")}
                      >
                        <Icon name="chart" />
                        {t("Аналитика", "Insights")}
                      </button>
                    )}
                  </div>
                </section>
              )}
              {(orgTab === "overview" || orgTab === "accounts") && (
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">
                        {t("ОРГАНИЗАЦИЯ", "ORGANIZATION")}
                      </p>
                      <h2>{selected.name}</h2>
                    </div>
                    <span className="tag">
                      {selected.current_role === "owner"
                        ? t("Владелец", "Owner")
                        : manager
                          ? t("Администратор", "Administrator")
                          : t("Участник", "Member")}
                    </span>
                  </div>
                  <div className="section-heading spaced">
                    <h3>{t("Финансы организации", "Organization finances")}</h3>
                    {manager && (
                      <button
                        className="secondary"
                        onClick={() => {
                          accountKey.current = crypto.randomUUID();
                          setAccountName("");
                          setAccountOpen(true);
                        }}
                      >
                        <Icon name="plus" size={16} />
                        {t("Открыть счёт", "Open account")}
                      </button>
                    )}
                  </div>
                  {selected.accounts?.length ? (
                    <div className="organization-accounts">
                      {selected.accounts.map((account) => (
                        <div key={account.id}>
                          <span className="account-icon">
                            <Icon name="wallet" />
                          </span>
                          <span>
                            <b>{account.name}</b>
                            <small>{account.public_number}</small>
                          </span>
                          <strong>
                            {money(account.available.amount, language)}
                          </strong>
                          <span className="account-actions">
                            {account.permissions.includes("history.read") && (
                              <button
                                className="text-button"
                                onClick={() => navigate("history", account.id)}
                              >
                                {t("История", "Activity")}
                              </button>
                            )}
                            {account.permissions.includes("transfer") && (
                              <>
                                <button
                                  className="secondary"
                                  onClick={() =>
                                    navigate("transfers", account.id)
                                  }
                                >
                                  {t("Перевести", "Transfer")}
                                </button>
                                <button
                                  className="text-button"
                                  onClick={() => betweenAccounts(account.id)}
                                >
                                  {t("Между счетами", "Between accounts")}
                                </button>
                              </>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty
                      icon="wallet"
                      title={t(
                        "Нет доступных счетов",
                        "No accessible accounts",
                      )}
                      description={
                        manager
                          ? t(
                              "Откройте первый счёт организации.",
                              "Open the organization's first account.",
                            )
                          : t(
                              "Владелец организации может предоставить вам доступ.",
                              "The organization owner can grant you access.",
                            )
                      }
                    />
                  )}
                </section>
              )}
              {orgTab === "members" && (
                <section className="panel">
                  <div className="section-heading">
                    <h3>{t("Участники и права", "Members and permissions")}</h3>
                    {manager && (
                      <button
                        className="secondary"
                        onClick={() => {
                          setRecipient(undefined);
                          setRole("member");
                          setGrants([
                            "accounts.read",
                            "balance.read",
                            "history.read",
                          ]);
                          setMemberOpen(true);
                        }}
                      >
                        <Icon name="plus" size={16} />
                        {t("Добавить участника", "Add member")}
                      </button>
                    )}
                  </div>
                  <div className="member-list">
                    {selected.members?.map((member) => (
                      <div key={member.user_id}>
                        <Avatar name={member.display_name} />
                        <span>
                          <b>
                            {member.bank_username
                              ? "@" + member.bank_username
                              : member.display_name}
                          </b>
                          <small>
                            {member.role === "owner"
                              ? t("Владелец", "Owner")
                              : member.role === "administrator"
                                ? t("Администратор", "Administrator")
                                : t("Участник", "Member")}{" "}
                            ·{" "}
                            {member.permissions.includes("transfer")
                              ? t("Может переводить", "Can transfer")
                              : t("Без права перевода", "Cannot transfer")}
                          </small>
                        </span>
                        {manager &&
                          member.role !== "owner" &&
                          (selected.current_role === "owner" ||
                            member.role !== "administrator") && (
                            <div className="button-row">
                              <button
                                className="text-button"
                                onClick={() => {
                                  setBusy(true);
                                  api
                                    .resolveRecipient({
                                      kind: "user",
                                      value: member.user_id,
                                    })
                                    .then((value) => {
                                      setRecipient(value);
                                      setRole(
                                        member.role === "administrator"
                                          ? "administrator"
                                          : "member",
                                      );
                                      setGrants(
                                        member.permissions.filter(
                                          (p) => p !== "account.manage",
                                        ),
                                      );
                                      setMemberOpen(true);
                                    })
                                    .catch((e) =>
                                      setError(localError(e, language)),
                                    )
                                    .finally(() => setBusy(false));
                                }}
                              >
                                {t("Права", "Permissions")}
                              </button>
                              <button
                                className="icon-button danger"
                                aria-label={t(
                                  "Удалить участника",
                                  "Remove member",
                                )}
                                onClick={() => setRemoving(member)}
                              >
                                <Icon name="close" size={17} />
                              </button>
                            </div>
                          )}
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {manager && orgTab === "hpay" && (
                <HPay key={selected.id} organization={selected} />
              )}
              {["payments", "payroll", "analytics", "exchange"].includes(
                orgTab,
              ) && (
                <Suspense fallback={<Loading />}>
                  <BusinessSections
                    key={selected.id}
                    organization={selected}
                    activeTab={orgTab}
                  />
                </Suspense>
              )}
              <Disclosure
                title={t("Дополнительные инструменты", "Additional tools")}
              >
                <div className="button-row">
                  <button
                    className="secondary"
                    onClick={() => {
                      navigate("settings");
                      location.hash = "/settings?tab=keys";
                    }}
                  >
                    <Icon name="code" />
                    {t("API-ключи и интеграции", "API keys and integrations")}
                  </button>
                  {manager && (
                    <button
                      className="secondary"
                      onClick={() => setOrgTab("members")}
                    >
                      <Icon name="shield" />
                      {t("Управление доступом", "Access management")}
                    </button>
                  )}
                </div>
                <p className="field-hint">
                  {t(
                    "Каждое финансовое действие требует отдельного разрешения. Доступ участника к просмотру счетов не позволяет отправлять средства.",
                    "Financial actions require separate permissions. Account visibility does not permit sending funds.",
                  )}
                </p>
              </Disclosure>
            </div>
          ) : (
            <section className="panel">
              <Empty
                icon="organization"
                title={t("Выберите организацию", "Choose an organization")}
                description={t(
                  "Посмотрите счета, участников и доступные действия.",
                  "View accounts, members and available actions.",
                )}
              />
            </section>
          )}
        </div>
      )}
      {createOpen && (
        <Modal
          title={t("Новая организация", "New organization")}
          onClose={() => {
            if (!busy) setCreateOpen(false);
          }}
        >
          <Notice error={error} />
          <form onSubmit={(event) => void create(event)}>
            <label>
              {t("Название организации", "Organization name")}
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                minLength={2}
                maxLength={120}
                required
              />
            </label>
            <button disabled={busy}>
              {t("Создать организацию", "Create organization")}
            </button>
          </form>
        </Modal>
      )}
      {accountOpen && (
        <Modal
          title={t("Счёт организации", "Organization account")}
          onClose={() => {
            if (!busy) setAccountOpen(false);
          }}
        >
          <Notice error={error} />
          <form onSubmit={(event) => void addAccount(event)}>
            <label>
              {t("Название счёта", "Account name")}
              <input
                value={accountName}
                onChange={(e) => setAccountName(e.target.value)}
                maxLength={80}
                required
              />
            </label>
            <button disabled={busy}>{t("Открыть счёт", "Open account")}</button>
          </form>
        </Modal>
      )}
      {memberOpen && (
        <Modal
          title={t("Участник организации", "Organization member")}
          onClose={() => {
            if (!busy) setMemberOpen(false);
          }}
        >
          <Notice error={error} />
          <form onSubmit={(event) => void addMember(event)}>
            <RecipientPicker value={recipient} onChange={setRecipient} />
            <label>
              {t("Роль", "Role")}
              <select
                value={role}
                onChange={(e) =>
                  setRole(e.target.value as "member" | "administrator")
                }
              >
                <option value="member">{t("Участник", "Member")}</option>
                {selected?.current_role === "owner" && (
                  <option value="administrator">
                    {t("Администратор", "Administrator")}
                  </option>
                )}
              </select>
            </label>
            {role === "member" ? (
              <fieldset>
                <legend>{t("Разрешения", "Permissions")}</legend>
                {permissionNames.map(([permission, ru, en]) => (
                  <label className="checkbox" key={permission}>
                    <input
                      type="checkbox"
                      checked={grants.includes(permission)}
                      onChange={(e) =>
                        setGrants((previous) =>
                          e.target.checked
                            ? [...previous, permission]
                            : previous.filter((p) => p !== permission),
                        )
                      }
                    />
                    {t(ru, en)}
                  </label>
                ))}
              </fieldset>
            ) : (
              <div className="info-box">
                <Icon name="shield" />
                {t(
                  "Администратор управляет участниками и всеми счетами организации, но не получает банковские права эмиссии.",
                  "An administrator manages members and organization accounts, but cannot issue currency.",
                )}
              </div>
            )}
            <button disabled={busy || !recipient?.id}>
              {t("Сохранить права", "Save permissions")}
            </button>
          </form>
        </Modal>
      )}
      {removing && (
        <Modal
          title={t("Отозвать доступ?", "Revoke access?")}
          onClose={() => {
            if (!busy) setRemoving(undefined);
          }}
        >
          <Notice error={error} />
          <p>
            {t(
              "Участник потеряет доступ к счетам и новым операциям этой организации.",
              "The member will lose access to this organization's accounts and new operations.",
            )}
          </p>
          <button
            className="danger-button"
            disabled={busy}
            onClick={() => void removeMember()}
          >
            {t("Отозвать доступ", "Revoke access")}
          </button>
        </Modal>
      )}
    </>
  );
}
