import { localize as ui, useI18n as useLocale } from "../../i18n";
import { useEffect, useState } from "react";
import {
  adminApi,
  query,
  type AdminUser,
  type Effective,
  type Role,
} from "../../admin-api";
import { useBank } from "../../bank-context";
import { Avatar, Modal } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { localError } from "../../i18n";
import { ActionDialog, NextPage, PageState, useAdminPage } from "./Shared";
export function Users({
  permissions,
  onAccounts,
}: {
  permissions: string[];
  onAccounts: (user: AdminUser) => void;
}) {
  useLocale();
  const { data: bank, refresh } = useBank();
  const [search, setSearch] = useState(""),
    [q, setQ] = useState(""),
    [cursor, setCursor] = useState("");
  const { data, error, reload } = useAdminPage<AdminUser>(
    "users?" + query({ query: q, cursor, limit: 20 }),
  );
  const [selected, setSelected] = useState<AdminUser>(),
    [effective, setEffective] = useState<Effective>(),
    [roles, setRoles] = useState<Role[]>([]),
    [roleId, setRoleId] = useState("");
  const [detailError, setDetailError] = useState(""),
    [action, setAction] = useState<{
      type: string;
      role?: Role;
    }>();
  useEffect(() => {
    let active = true;
    if (!selected) return;
    setEffective(undefined);
    setDetailError("");
    adminApi
      .effective(selected.id)
      .then((x) => {
        if (active) setEffective(x);
      })
      .catch((e) => {
        if (active) setDetailError(localError(e));
      });
    if (permissions.includes("admin.roles.manage"))
      adminApi
        .roles()
        .then((x) => {
          if (active) setRoles(x.items);
        })
        .catch((e) => {
          if (active) setDetailError(localError(e));
        });
    return () => {
      active = false;
    };
  }, [selected, permissions]);
  async function perform(reason: string) {
    if (!selected || !action) return;
    const id = selected.id;
    if (action.type === "block" || action.type === "activate") {
      await adminApi.write(`users/${id}/state`, {
        status: action.type === "block" ? "blocked" : "active",
        reason,
      });
      setSelected({
        ...selected,
        status: action.type === "block" ? "blocked" : "active",
      });
    } else if (action.role) {
      await adminApi.write(
        `users/${id}/roles/${action.role.id}${action.type === "revoke" ? "/revoke" : ""}`,
        { confirmed: true, reason },
        action.type === "revoke" ? "POST" : "PUT",
      );
      setEffective(await adminApi.effective(id));
    }
    reload();
    await refresh();
  }
  return (
    <>
      <form
        className="admin-toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(search);
          setCursor("");
        }}
      >
        <input
          aria-label={ui("Поиск пользователей")}
          placeholder={ui("Банковский ник, Discord ID, Minecraft или имя")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button>{ui("Найти")}</button>
      </form>
      <PageState
        loading={!data}
        error={error}
        empty={!!data && !data.items.length}
      />
      {data && (
        <>
          <div className="admin-table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">{ui("Пользователь")}</th>
                  <th scope="col">{ui("Связанные аккаунты")}</th>
                  <th scope="col">{ui("Статус")}</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((u) => (
                  <tr key={u.id}>
                    <td data-label={ui("Пользователь")}>
                      <b>{u.display_name}</b>
                      <small>@{u.bank_username}</small>
                    </td>
                    <td data-label={ui("Связанные аккаунты")}>
                      {u.minecraft_nickname || "—"}
                      <small>{u.discord_id || ui("Discord не привязан")}</small>
                    </td>
                    <td data-label={ui("Статус")}>
                      <span className="tag">
                        {u.status === "active"
                          ? ui("Активен")
                          : u.status === "blocked"
                            ? ui("Заблокирован")
                            : ui("Архив")}
                      </span>
                    </td>
                    <td data-label={ui("Действия")}>
                      <button
                        className="secondary"
                        onClick={() => setSelected(u)}
                      >
                        {ui("Открыть")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <NextPage
            next={data.next_cursor}
            onNext={() => setCursor(data.next_cursor!)}
            onFirst={() => setCursor("")}
          />
        </>
      )}
      {selected && !action && (
        <Modal
          title={ui("Карточка пользователя")}
          onClose={() => setSelected(undefined)}
        >
          <div className="profile-heading">
            <Avatar name={selected.display_name} large />
            <div>
              <h3>{selected.display_name}</h3>
              <p>@{selected.bank_username}</p>
            </div>
          </div>
          <Notice error={detailError} />
          <p>
            Discord: {selected.discord_id || ui("не привязан")} · Minecraft:{" "}
            {selected.minecraft_nickname || ui("не привязан")}
          </p>
          {permissions.includes("admin.accounts.read") && (
            <button
              className="secondary"
              onClick={() => {
                onAccounts(selected);
                setSelected(undefined);
              }}
            >
              {ui("Счета пользователя")}
            </button>
          )}
          {effective && (
            <>
              <h3>{ui("Эффективные административные права")}</h3>
              <div className="permission-tags">
                {effective.permissions.length
                  ? effective.permissions.map((p) => (
                      <span className="tag" key={p}>
                        {p}
                      </span>
                    ))
                  : ui("Административных прав нет")}
              </div>
              <h3>{ui("Назначенные роли")}</h3>
              {effective.roles.map((r) => (
                <div className="delivery-row" key={r.id}>
                  <b>{r.name}</b>
                  {permissions.includes("admin.roles.manage") &&
                    selected.id !== bank.profile.id && (
                      <button
                        className="text-button danger"
                        onClick={() => setAction({ type: "revoke", role: r })}
                      >
                        {ui("Снять роль")}
                      </button>
                    )}
                </div>
              ))}
              {!!effective.legacy_permissions.length && (
                <p className="field-hint">
                  {ui("Сохранены прямые права Banking System 2.1:")}{" "}
                  {effective.legacy_permissions
                    .map((x) => x.permission)
                    .join(", ")}
                  {ui(". Снятие роли их не отменяет.")}
                </p>
              )}
            </>
          )}
          {selected.id !== bank.profile.id &&
            permissions.includes("admin.roles.manage") && (
              <div className="admin-toolbar">
                <select
                  aria-label={ui("Назначаемая роль")}
                  value={roleId}
                  onChange={(e) => setRoleId(e.target.value)}
                >
                  <option value="">{ui("Выберите роль")}</option>
                  {roles.map((r) => (
                    <option value={r.id} key={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
                <button
                  disabled={!roleId}
                  onClick={() =>
                    setAction({
                      type: "assign",
                      role: roles.find((r) => r.id === roleId),
                    })
                  }
                >
                  {ui("Назначить")}
                </button>
              </div>
            )}
          {selected.id !== bank.profile.id &&
            permissions.includes("admin.users") && (
              <button
                className="danger-button"
                onClick={() =>
                  setAction({
                    type: selected.status === "active" ? "block" : "activate",
                  })
                }
              >
                {selected.status === "active"
                  ? ui("Заблокировать профиль")
                  : ui("Разблокировать профиль")}
              </button>
            )}
        </Modal>
      )}
      {action && selected && (
        <ActionDialog
          title={
            action.role
              ? action.type === "revoke"
                ? ui("Снять роль?")
                : ui("Назначить роль?")
              : action.type === "block"
                ? ui("Заблокировать пользователя?")
                : ui("Разблокировать пользователя?")
          }
          onClose={() => setAction(undefined)}
          onConfirm={perform}
        >
          <p>
            {selected.display_name} · @{selected.bank_username}
          </p>
          {action.role && (
            <p>
              {ui("Роль: ")}
              <b>{action.role.name}</b>
            </p>
          )}
        </ActionDialog>
      )}
    </>
  );
}
