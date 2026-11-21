import { localize as ui, useI18n as useLocale } from "../../i18n";
import { useEffect, useState } from "react";
import { adminApi, query, type AdminUser, type Role } from "../../admin-api";
import { Modal } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { localError } from "../../i18n";
import {
  ActionDialog,
  NextPage,
  PageState,
  UserPicker,
  useAdminPage,
} from "./Shared";
export function Roles({ permissions }: { permissions: string[] }) {
  useLocale();
  const [q, setQ] = useState(""),
    [offset, setOffset] = useState(0);
  const { data, error, reload } = useAdminPage<Role>(
    "roles?" + query({ q, offset }),
  );
  const [registry, setRegistry] = useState<
      {
        permission: string;
        description: string;
      }[]
    >([]),
    [editing, setEditing] = useState<Partial<Role>>(),
    [editError, setEditError] = useState(""),
    [busy, setBusy] = useState(false);
  const [members, setMembers] = useState<Role>(),
    [memberOffset, setMemberOffset] = useState(0);
  const [assigning, setAssigning] = useState<Role>(),
    [target, setTarget] = useState<AdminUser>();
  const memberPage = useAdminPage<AdminUser>(
    members ? `roles/${members.id}/users?offset=${memberOffset}` : null,
  );
  useEffect(() => {
    adminApi
      .get<
        {
          permission: string;
          description: string;
        }[]
      >("permissions")
      .then(setRegistry)
      .catch((e) => setEditError(localError(e)));
  }, []);
  async function save() {
    if (!editing) return;
    setBusy(true);
    setEditError("");
    try {
      await adminApi.write(
        editing.id ? "roles/" + editing.id : "roles",
        {
          name: editing.name,
          description: editing.description || "",
          permissions: editing.permissions || [],
        },
        editing.id ? "PATCH" : "POST",
      );
      setEditing(undefined);
      reload();
    } catch (e) {
      setEditError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="admin-toolbar">
        <input
          aria-label={ui("Поиск ролей")}
          placeholder={ui("Найти роль")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
        />
        <button
          onClick={() => {
            setEditError("");
            setEditing({ name: "", description: "", permissions: [] });
          }}
        >
          {ui("Создать роль")}
        </button>
      </div>
      <PageState
        loading={!data}
        error={error}
        empty={!!data && !data.items.length}
      />
      {data && (
        <>
          <div className="admin-role-grid">
            {data.items.map((r) => (
              <section className="panel" key={r.id}>
                <span className="eyebrow">
                  {r.system_key
                    ? ui("СИСТЕМНЫЙ ШАБЛОН")
                    : ui("ПОЛЬЗОВАТЕЛЬСКАЯ РОЛЬ")}
                </span>
                <h3>{r.system_key ? ui(r.name) : r.name}</h3>
                <p>{r.system_key ? ui(r.description) : r.description}</p>
                <small>
                  {r.permissions.length}
                  {ui(" разрешений · пользователей: ")}
                  {r.members}
                </small>
                <details>
                  <summary>{ui("Полномочия роли")}</summary>
                  <div className="permission-tags">
                    {r.permissions.map((p) => (
                      <span className="tag" key={p}>
                        {p}
                      </span>
                    ))}
                  </div>
                </details>
                <div className="admin-toolbar">
                  {permissions.includes("admin.users.read") && (
                    <button
                      className="secondary"
                      onClick={() => {
                        setAssigning(r);
                        setTarget(undefined);
                      }}
                    >
                      {ui("Назначить пользователю")}
                    </button>
                  )}
                  <button
                    className="secondary"
                    onClick={() => {
                      setMembers(r);
                      setMemberOffset(0);
                    }}
                  >
                    {ui("Участники")}
                  </button>
                  {!r.system_key && (
                    <button
                      className="secondary"
                      onClick={() => {
                        setEditError("");
                        setEditing(r);
                      }}
                    >
                      {ui("Изменить")}
                    </button>
                  )}
                </div>
              </section>
            ))}
          </div>
          <NextPage
            next={data.next_offset}
            onNext={() => setOffset(data.next_offset!)}
            onFirst={() => setOffset(0)}
          />
        </>
      )}
      {assigning && !target && (
        <Modal
          title={ui("Назначить роль «") + assigning.name + "»"}
          onClose={() => setAssigning(undefined)}
        >
          <UserPicker onPick={setTarget} />
        </Modal>
      )}
      {assigning && target && (
        <ActionDialog
          title={ui("Назначить административную роль?")}
          onClose={() => {
            setAssigning(undefined);
            setTarget(undefined);
          }}
          onConfirm={async (reason) => {
            await adminApi.write(
              `users/${target.id}/roles/${assigning.id}`,
              { reason, confirmed: true },
              "PUT",
            );
            reload();
          }}
        >
          <p>
            {target.display_name} · @{target.bank_username}
          </p>
          <p>
            {ui("Роль: ")}
            <b>{assigning.name}</b>
          </p>
        </ActionDialog>
      )}
      {editing && (
        <Modal
          title={editing.id ? ui("Изменить роль") : ui("Новая роль")}
          onClose={() => setEditing(undefined)}
        >
          <Notice error={editError} />
          <label>
            {ui("Название")}
            <input
              maxLength={80}
              value={editing.name || ""}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            />
          </label>
          <label>
            {ui("Описание")}
            <textarea
              maxLength={500}
              value={editing.description || ""}
              onChange={(e) =>
                setEditing({ ...editing, description: e.target.value })
              }
            />
          </label>
          <fieldset>
            <legend>{ui("Разрешения")}</legend>
            {registry.map((p) => (
              <label className="checkbox" key={p.permission}>
                <input
                  type="checkbox"
                  disabled={!permissions.includes(p.permission)}
                  checked={editing.permissions?.includes(p.permission) || false}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      permissions: e.target.checked
                        ? [...(editing.permissions || []), p.permission]
                        : (editing.permissions || []).filter(
                            (x) => x !== p.permission,
                          ),
                    })
                  }
                />
                <span>
                  {ui(p.description)}
                  <small>{p.permission}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <p className="field-hint">
            {ui(
              "Финансовые действия дополнительно проверяет C++-ядро. Назначение роли не выдаёт финансовые полномочия.",
            )}
          </p>
          <button
            disabled={busy || !editing.name?.trim()}
            onClick={() => void save()}
          >
            {busy ? ui("Сохраняем…") : ui("Сохранить роль")}
          </button>
        </Modal>
      )}
      {members && (
        <Modal
          title={ui("Пользователи роли «") + members.name + "»"}
          onClose={() => setMembers(undefined)}
        >
          <PageState
            loading={!memberPage.data}
            error={memberPage.error}
            empty={!!memberPage.data && !memberPage.data.items.length}
          />
          {memberPage.data?.items.map((u) => (
            <div className="delivery-row" key={u.id}>
              {u.display_name} <b>@{u.bank_username}</b>
            </div>
          ))}
          <NextPage
            next={memberPage.data?.next_offset}
            onNext={() => setMemberOffset(memberPage.data!.next_offset!)}
            onFirst={() => setMemberOffset(0)}
          />
        </Modal>
      )}
    </>
  );
}
