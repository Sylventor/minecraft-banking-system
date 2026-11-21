import { localize as ui, useI18n as useLocale } from "../../i18n";
import { useState } from "react";
import { adminApi, query, type AdminUser } from "../../admin-api";
import type { AuditEvent, Investigation } from "../../types";
import { dateTime, localError } from "../../i18n";
import { Notice } from "../../components/Notice";
import { Modal } from "../../components/BankUI";
import {
  ActionDialog,
  NextPage,
  PageState,
  UserPicker,
  useAdminPage,
} from "./Shared";
export function Audit() {
  useLocale();
  const [actor, setActor] = useState<AdminUser>(),
    [action, setAction] = useState(""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [cursor, setCursor] = useState("");
  const { data, error } = useAdminPage<AuditEvent>(
    "audit?" +
      query({
        actor_id: actor?.id,
        action,
        cursor,
        from_date: from ? from + "T00:00:00Z" : "",
        to_date: to ? to + "T23:59:59Z" : "",
        limit: 25,
      }),
  );
  return (
    <>
      <UserPicker
        label={ui("Действия пользователя")}
        onPick={(u) => {
          setActor(u);
          setCursor("");
        }}
      />
      {actor && (
        <button
          className="text-button"
          onClick={() => {
            setActor(undefined);
            setCursor("");
          }}
        >
          {ui("Сбросить фильтр @")}
          {actor.bank_username}
        </button>
      )}
      <div className="admin-toolbar">
        <input
          aria-label={ui("Фильтр аудита")}
          placeholder={ui("Действие: role, terms, money…")}
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setCursor("");
          }}
        />
        <label>
          {ui("От")}
          <input
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setCursor("");
            }}
          />
        </label>
        <label>
          {ui("До")}
          <input
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setCursor("");
            }}
          />
        </label>
      </div>
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
                  <th scope="col">{ui("Действие")}</th>
                  <th scope="col">{ui("Объект")}</th>
                  <th scope="col">{ui("Дата")}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((x) => (
                  <tr key={x.id}>
                    <td data-label={ui("Действие")}>
                      <b>{x.action}</b>
                      <small>{x.actor_id}</small>
                    </td>
                    <td data-label={ui("Объект")}>
                      {x.target_type}
                      <small>{x.target_id}</small>
                    </td>
                    <td data-label={ui("Дата")}>
                      {dateTime(x.created_at, undefined)}
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
    </>
  );
}
export function Investigations({ editable }: { editable: boolean }) {
  useLocale();
  const [cursor, setCursor] = useState("");
  const { data, error, reload } = useAdminPage<Investigation>(
    "investigations?" + query({ limit: 25, paginated: "true", cursor }),
  );
  const [selected, setSelected] = useState<Investigation>(),
    [notes, setNotes] = useState<
      {
        id: string;
        body: string;
        created_at: string;
      }[]
    >([]),
    [adding, setAdding] = useState(false),
    [closing, setClosing] = useState(false),
    [detailError, setDetailError] = useState("");
  async function view(item: Investigation) {
    setSelected(item);
    setNotes([]);
    setDetailError("");
    try {
      setNotes(await adminApi.get(`investigations/${item.id}/notes`));
    } catch (e) {
      setDetailError(localError(e));
    }
  }
  return (
    <>
      <PageState
        loading={!data}
        error={error}
        empty={!!data && !data.items.length}
      />
      {data?.items.map((x) => (
        <div className="delivery-row" key={x.id}>
          <span>
            <b>
              {x.status === "open" ? ui("Открыто") : ui("Закрыто")} ·{" "}
              {x.subject_type}
            </b>
            <small>
              {x.subject_id} · {dateTime(x.created_at, undefined)}
            </small>
          </span>
          <button className="secondary" onClick={() => void view(x)}>
            {ui("Материалы")}
          </button>
        </div>
      ))}
      <NextPage
        next={data?.next_cursor}
        onNext={() => setCursor(data!.next_cursor!)}
        onFirst={() => setCursor("")}
      />
      {selected && !adding && !closing && (
        <Modal
          title={ui("Расследование")}
          onClose={() => setSelected(undefined)}
        >
          <Notice error={detailError} />
          <p>
            {ui("Объект: ")}
            {selected.subject_id}
          </p>
          {notes.map((n) => (
            <div className="panel" key={n.id}>
              <p>{n.body}</p>
              <small>{dateTime(n.created_at, undefined)}</small>
            </div>
          ))}
          {editable && selected.status === "open" && (
            <div className="admin-toolbar">
              <button onClick={() => setAdding(true)}>
                {ui("Добавить заметку")}
              </button>
              <button className="secondary" onClick={() => setClosing(true)}>
                {ui("Закрыть расследование")}
              </button>
            </div>
          )}
        </Modal>
      )}
      {adding && selected && (
        <ActionDialog
          title={ui("Добавить заметку")}
          onClose={() => setAdding(false)}
          onConfirm={async (body) => {
            await adminApi.write(`investigations/${selected.id}/notes`, {
              body,
            });
            await view(selected);
          }}
        >
          <p>{ui("Заметка сохранится в истории расследования.")}</p>
        </ActionDialog>
      )}
      {closing && selected && (
        <ActionDialog
          title={ui("Закрыть расследование?")}
          reasonRequired={false}
          onClose={() => setClosing(false)}
          onConfirm={async () => {
            await adminApi.write(`investigations/${selected.id}/close`, {});
            setSelected(undefined);
            reload();
          }}
        >
          <p>{ui("Добавление новых заметок после закрытия недоступно.")}</p>
        </ActionDialog>
      )}
    </>
  );
}
