import { useEffect, useState } from "react";
import { adminApi } from "../../admin-api";
import { dateTime, localError, localize as ui, useI18n } from "../../i18n";
import { Notice } from "../../components/Notice";
import { ActionDialog } from "./Shared";

type PrivacyCase = {
  id: string;
  user_id: string;
  kind: string;
  status: string;
  due_at: string;
  bank_username?: string;
  notification_status: string;
};
type Detail = {
  request: PrivacyCase;
  auth_tables: string[];
  finance: { tables: string[]; blockers: Record<string, unknown> };
  blockers: Record<string, unknown>;
};
const kinds = () => ({
  access: ui("Доступ к данным"),
  rectification: ui("Исправление данных"),
  erasure: ui("Удаление данных"),
  restriction: ui("Ограничение обработки"),
  objection: ui("Возражение против обработки"),
  portability: ui("Переносимость данных"),
  consent_withdrawal: ui("Отзыв согласия"),
});
const statuses = () => ({
  received: ui("Получен"),
  verified: ui("Заявитель проверен"),
  approved: ui("Одобрен"),
  rejected: ui("Отклонён"),
  processing: ui("Выполняется"),
  blocked: ui("Требуется проверка"),
  completed: ui("Завершён"),
});
export function PrivacyManager({ permissions }: { permissions: string[] }) {
  useI18n();
  const [items, setItems] = useState<PrivacyCase[]>([]),
    [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState<Detail>(),
    [error, setError] = useState("");
  const [query, setQuery] = useState(""),
    [subject, setSubject] = useState("");
  const [kind, setKind] = useState("access"),
    [source, setSource] = useState("support"),
    [ticket, setTicket] = useState("");
  const [action, setAction] = useState<string>(),
    [review, setReview] = useState(false),
    [basis, setBasis] = useState("");
  const [schema, setSchema] = useState("auth"),
    [table, setTable] = useState(""),
    [page, setPage] = useState(0);
  const [holdUntil, setHoldUntil] = useState(""),
    [busy, setBusy] = useState(false);
  const [holds, setHolds] = useState<
    { id: string; user_id: string; basis: string; expires_at: string }[]
  >([]);
  const manage = permissions.includes("admin.privacy.manage");
  async function load() {
    const [requests, activeHolds] = await Promise.all([
      adminApi.get<{ items: PrivacyCase[] }>(
        "privacy/requests?offset=" + offset,
      ),
      adminApi.get<{ items: typeof holds }>("privacy/holds"),
    ]);
    setItems(requests.items);
    setHolds(activeHolds.items);
  }
  useEffect(() => {
    let live = true;
    adminApi
      .get<{ items: PrivacyCase[] }>("privacy/requests?offset=" + offset)
      .then((x) => {
        if (live) setItems(x.items);
      })
      .catch((e) => {
        if (live) setError(localError(e));
      });
    adminApi
      .get<{ items: typeof holds }>("privacy/holds")
      .then((x) => {
        if (live) setHolds(x.items);
      })
      .catch((e) => {
        if (live) setError(localError(e));
      });
    return () => {
      live = false;
    };
  }, [offset]);
  async function run(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string) {
    const value = await adminApi.get<Detail>("privacy/requests/" + id);
    setDetail(value);
    setTable("");
    setPage(0);
    setReview(false);
    setBasis("");
  }
  return (
    <>
      <Notice error={error} />
      <p>
        {ui(
          "Запросы принимаются через поддержку или privacy@example.invalid. Проверяйте принадлежность аккаунта, не запрашивая лишние документы.",
        )}
      </p>
      {manage && (
        <section className="panel">
          <h3>{ui("Зарегистрировать запрос")}</h3>
          <label>
            {ui("Банковское имя или UUID")}
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <button
            disabled={busy || query.trim().length < 3}
            onClick={() =>
              void run(async () => {
                const found = await adminApi.get<{ items: { id: string }[] }>(
                  "privacy/subjects?q=" + encodeURIComponent(query.trim()),
                );
                setSubject(found.items[0]?.id || "");
                if (!found.items.length)
                  throw new Error(ui("Пользователь не найден"));
              })
            }
          >
            {ui("Найти пользователя")}
          </button>
          {subject && (
            <p>
              <code>{subject}</code>
            </p>
          )}
          <label>
            {ui("Вид запроса")}
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(kinds()).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            {ui("Канал обращения")}
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="support">{ui("Поддержка")}</option>
              <option value="email">Email</option>
            </select>
          </label>
          <label>
            {ui("UUID обращения в поддержку (необязательно)")}
            <input value={ticket} onChange={(e) => setTicket(e.target.value)} />
          </label>
          <button
            disabled={busy || !subject}
            onClick={() =>
              void run(async () => {
                const created = await adminApi.write<{ id: string }>(
                  "privacy/requests",
                  { user_id: subject, kind, source, ticket_id: ticket || null },
                );
                await load();
                await open(created.id);
              })
            }
          >
            {ui("Зарегистрировать запрос")}
          </button>
        </section>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{ui("Пользователь")}</th>
              <th>{ui("Вид запроса")}</th>
              <th>{ui("Статус")}</th>
              <th>{ui("Срок ответа")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>
                  <button
                    disabled={busy}
                    onClick={() => void run(() => open(item.id))}
                  >
                    {item.bank_username || item.user_id}
                  </button>
                </td>
                <td>{kinds()[item.kind as keyof ReturnType<typeof kinds>]}</td>
                <td>
                  {statuses()[item.status as keyof ReturnType<typeof statuses>]}
                </td>
                <td>{dateTime(item.due_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        disabled={busy || offset === 0}
        onClick={() => setOffset(Math.max(0, offset - 25))}
      >
        {ui("Назад")}
      </button>
      <button
        disabled={busy || items.length < 25}
        onClick={() => setOffset(offset + 25)}
      >
        {ui("Далее")}
      </button>
      {detail && (
        <section className="panel">
          <h3>
            {kinds()[detail.request.kind as keyof ReturnType<typeof kinds>]}
          </h3>
          <code>{detail.request.id}</code>
          <p>{ui("Блокирующие условия")}</p>
          <pre>
            {JSON.stringify(
              { ...detail.blockers, ...detail.finance.blockers },
              null,
              2,
            )}
          </pre>
          <p>
            {ui(
              "После удаления профиля сохраняются минимальные псевдонимные связи финансового журнала. Основание и копии данных требуют проверки сотрудником.",
            )}
          </p>
          {manage && (
            <>
              <label>
                {ui("Основание сохранения необходимых записей")}
                <textarea
                  value={basis}
                  maxLength={1000}
                  onChange={(e) => setBasis(e.target.value)}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={review}
                  onChange={(e) => setReview(e.target.checked)}
                />
                {ui(
                  "Проверены копии данных, споры и фактическое исполнение запроса",
                )}
              </label>
              {[
                ["verify", ui("Проверить заявителя")],
                ["approve", ui("Одобрить")],
                ["reject", ui("Отклонить")],
                ["complete", ui("Завершить после исполнения")],
                ["notified", ui("Результат сообщён заявителю")],
              ].map(([key, label]) => (
                <button
                  key={key}
                  disabled={
                    busy ||
                    (key === "approve" &&
                      detail.request.kind === "erasure" &&
                      !permissions.includes("admin.privacy.erase"))
                  }
                  onClick={() => setAction(key)}
                >
                  {label}
                </button>
              ))}
              <p>
                {ui(
                  "Удаление и ограничения выполняются обслуживанием после проверки резервной копии. Завершение удаления ожидает очистки журнала банкоматов; результат сообщите через исходный канал обращения.",
                )}
              </p>
              <label>
                {ui("Сохранить данные до (UTC)")}
                <input
                  type="datetime-local"
                  value={holdUntil}
                  onChange={(e) => setHoldUntil(e.target.value)}
                />
              </label>
              <button
                disabled={busy || !holdUntil || basis.trim().length < 3}
                onClick={() => setAction("hold")}
              >
                {ui("Приостановить удаление данных")}
              </button>
            </>
          )}
          {permissions.includes("admin.privacy.export") && (
            <>
              <h3>{ui("Экспорт для проверки сотрудником")}</h3>
              <p>
                {ui(
                  "Перед передачей заявителю исключите чужие данные и внутренние сведения. Файл не содержит секретов авторизации.",
                )}
              </p>
              <select
                aria-label={ui("Источник данных")}
                value={schema}
                onChange={(e) => {
                  setSchema(e.target.value);
                  setTable("");
                  setPage(0);
                }}
              >
                <option value="auth">auth</option>
                <option value="finance">finance</option>
              </select>
              <select
                aria-label={ui("Таблица")}
                value={table}
                onChange={(e) => {
                  setTable(e.target.value);
                  setPage(0);
                }}
              >
                <option value="">{ui("Выберите таблицу")}</option>
                {(schema === "auth"
                  ? detail.auth_tables
                  : detail.finance.tables
                ).map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
              <button
                disabled={busy || !table}
                onClick={() =>
                  void run(async () => {
                    const data = await adminApi.write<{
                      next_offset: number | null;
                    }>("privacy/requests/" + detail.request.id + "/export", {
                      schema_name: schema,
                      table,
                      offset: page,
                    });
                    const url = URL.createObjectURL(
                      new Blob([JSON.stringify(data, null, 2)], {
                        type: "application/json",
                      }),
                    );
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = `privacy-${schema}-${table}-${page}.json`;
                    link.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                    if (data.next_offset !== null) setPage(data.next_offset);
                  })
                }
              >
                {ui("Скачать страницу экспорта")}
              </button>
              <p>
                {ui("Смещение")}: {page}
              </p>
            </>
          )}
        </section>
      )}
      {holds.length > 0 && (
        <section className="panel">
          <h3>{ui("Приостановки удаления")}</h3>
          {holds.map((hold) => (
            <p key={hold.id}>
              <code>{hold.user_id}</code> · {dateTime(hold.expires_at)} ·{" "}
              {hold.basis}{" "}
              {manage && (
                <button
                  disabled={busy}
                  onClick={() => {
                    setSubject(hold.id);
                    setAction("release");
                  }}
                >
                  {ui("Снять приостановку")}
                </button>
              )}
            </p>
          ))}
        </section>
      )}
      {action && (
        <ActionDialog
          title={ui("Обработка запроса о данных")}
          onClose={() => setAction(undefined)}
          onConfirm={async (reason) => {
            if (action === "hold" && detail)
              await adminApi.write("privacy/holds", {
                user_id: detail.request.user_id,
                basis: reason,
                expires_at: holdUntil + ":00Z",
              });
            else if (action === "release")
              await adminApi.write("privacy/holds/" + subject + "/release", {});
            else if (detail) {
              await adminApi.write(
                "privacy/requests/" + detail.request.id + "/actions",
                {
                  action,
                  reason,
                  confirmed: true,
                  review_confirmed: review,
                  retention_basis: basis,
                },
              );
              await open(detail.request.id);
            }
            await load();
          }}
        >
          <p>{ui("Подтвердите проверку оснований и выбранного аккаунта.")}</p>
        </ActionDialog>
      )}
    </>
  );
}
