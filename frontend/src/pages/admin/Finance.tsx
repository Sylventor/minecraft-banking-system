import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../../i18n";
import { useRef, useState } from "react";
import { api } from "../../api";
import {
  adminApi,
  query,
  type AdminAccount,
  type AdminOperation,
  type AdminUser,
} from "../../admin-api";
import { Modal } from "../../components/BankUI";
import {
  dateTime,
  localError,
  money,
  normalizeAmount,
  operationName,
} from "../../i18n";
import {
  AccountPicker,
  ActionDialog,
  NextPage,
  PageState,
  UserPicker,
  useAdminPage,
} from "./Shared";
export function Accounts({
  permissions,
  owner,
  onHistory,
}: {
  permissions: string[];
  owner?: AdminUser;
  onHistory: (a: AdminAccount) => void;
}) {
  useLocale();
  const [q, setQ] = useState(""),
    [cursor, setCursor] = useState(""),
    [filter, setFilter] = useState(owner);
  const { data, error, reload } = useAdminPage<AdminAccount>(
    "accounts?" + query({ q, cursor, owner_id: filter?.id, limit: 20 }),
  );
  const [selected, setSelected] = useState<AdminAccount>(),
    [state, setState] = useState("frozen"),
    [confirm, setConfirm] = useState(false);
  return (
    <>
      <div className="admin-toolbar">
        <input
          aria-label={ui("Поиск счетов")}
          placeholder={ui("Номер HB-, название или организация")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setCursor("");
          }}
        />
        {filter && (
          <button
            className="secondary"
            onClick={() => {
              setFilter(undefined);
              setCursor("");
            }}
          >
            {ui("Сбросить владельца @")}
            {filter.bank_username}
          </button>
        )}
      </div>
      {permissions.includes("admin.users.read") && (
        <UserPicker
          label={ui("Фильтр по владельцу")}
          onPick={(u) => {
            setFilter(u);
            setCursor("");
          }}
        />
      )}
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
                  <th scope="col">{ui("Счёт")}</th>
                  <th scope="col">{ui("Владелец")}</th>
                  <th scope="col">{ui("Деньги")}</th>
                  <th scope="col">{ui("Статус")}</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id}>
                    <td data-label={ui("Счёт")}>
                      <b>{a.name}</b>
                      <small>{a.public_number}</small>
                    </td>
                    <td data-label={ui("Владелец")}>{a.owner}</td>
                    <td data-label={ui("Деньги")}>
                      {money(a.balance, undefined)}
                      <small>
                        {ui("Резерв: ")}
                        {money(a.held, undefined)}
                      </small>
                    </td>
                    <td data-label={ui("Статус")}>
                      {a.status === "active"
                        ? ui("Активен")
                        : a.status === "frozen"
                          ? ui("Заморожен")
                          : ui("Закрыт")}
                    </td>
                    <td data-label={ui("Действия")}>
                      <button
                        className="secondary"
                        onClick={() => setSelected(a)}
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
      {selected && !confirm && (
        <Modal title={selected.name} onClose={() => setSelected(undefined)}>
          <p>{selected.public_number}</p>
          <p>
            {ui("Владелец: ")}
            {selected.owner}
          </p>
          <h3>{money(selected.balance, undefined)}</h3>
          <p>
            {ui("В резерве: ")}
            {money(selected.held, undefined)}
          </p>
          {permissions.includes("admin.transactions.read") && (
            <button
              className="secondary"
              onClick={() => {
                onHistory(selected);
                setSelected(undefined);
              }}
            >
              {ui("История операций")}
            </button>
          )}
          {permissions.includes("admin.accounts.manage") && (
            <>
              <label>
                {ui("Новый статус")}
                <select
                  value={state}
                  onChange={(e) => setState(e.target.value)}
                >
                  <option value="frozen">{ui("Заморожен")}</option>
                  <option value="active">{ui("Активен")}</option>
                  <option value="closed">{ui("Закрыт")}</option>
                </select>
              </label>
              <button
                className="danger-button"
                onClick={() => setConfirm(true)}
              >
                {ui("Изменить статус")}
              </button>
            </>
          )}
        </Modal>
      )}
      {confirm && selected && (
        <ActionDialog
          title={ui("Изменить статус счёта?")}
          onClose={() => setConfirm(false)}
          onConfirm={async (reason) => {
            await adminApi.write(`accounts/${selected.id}/state`, {
              state,
              reason,
              confirmed: true,
            });
            setSelected(undefined);
            reload();
          }}
        >
          <p>
            {selected.name} · {selected.public_number}
          </p>
          <p>
            {ui(
              "Закрыть можно только пустой счёт. Закрытые счета нельзя открыть заново.",
            )}
          </p>
        </ActionDialog>
      )}
    </>
  );
}
export function MoneyAction({
  account,
  operation,
  onClose,
  onDone,
}: {
  account?: AdminAccount;
  operation?: AdminOperation;
  onClose: () => void;
  onDone: () => void;
}) {
  useLocale();
  const [amount, setAmount] = useState(""),
    [reference, setReference] = useState(""),
    [ready, setReady] = useState(false);
  const key = useRef({ payload: "", key: "" });
  async function perform(reason: string) {
    const canonical = normalizeAmount(amount);
    if (!canonical) return;
    const payload = JSON.stringify({
      amount: canonical,
      reference,
      reason,
      account: account?.id,
      operation: operation?.id,
    });
    if (key.current.payload !== payload)
      key.current = { payload, key: crypto.randomUUID() };
    if (operation)
      await api.refund(
        operation.destination_account_id,
        operation.id,
        canonical,
        reason,
        key.current.key,
      );
    else if (account)
      await api.issue(
        account.id,
        canonical,
        reason,
        reference,
        key.current.key,
      );
    onDone();
    onClose();
  }
  if (ready)
    return (
      <ActionDialog
        title={
          operation ? ui("Подтвердить возврат?") : ui("Подтвердить эмиссию?")
        }
        onClose={() => setReady(false)}
        onConfirm={perform}
      >
        <h3>{money(normalizeAmount(amount) || "0.00", undefined)}</h3>
        <p>{account?.name || operation?.destination_name}</p>
      </ActionDialog>
    );
  return (
    <Modal
      title={operation ? ui("Возврат по операции") : ui("Зачисление средств")}
      onClose={onClose}
    >
      <p>
        {account
          ? account.name + " · " + account.public_number
          : uit(
              "Операция #{0} · {1}",
              operation?.id,
              operation?.destination_name,
            )}
      </p>
      <label>
        {ui("Сумма (ДБ)")}
        <input
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          maxLength={24}
        />
      </label>
      {!operation && (
        <label>
          {ui("Основание эмиссии")}
          <input
            value={reference}
            onChange={(e) => setReference(e.target.value)}
            maxLength={500}
          />
        </label>
      )}
      <p className="field-hint">
        {ui(
          "Действие требует отдельных финансовых полномочий ядра и будет записано в аудит. При задержке ответа повторите то же подтверждение.",
        )}
      </p>
      <button
        disabled={!normalizeAmount(amount) || (!operation && !reference.trim())}
        onClick={() => setReady(true)}
      >
        {ui("Проверить и подтвердить")}
      </button>
    </Modal>
  );
}
export function Transactions({
  permissions,
  account,
  issuances = false,
}: {
  permissions: string[];
  account?: AdminAccount;
  issuances?: boolean;
}) {
  useLocale();
  const [q, setQ] = useState(""),
    [cursor, setCursor] = useState(""),
    [type, setType] = useState(issuances ? "issue" : ""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState("");
  const [selected, setSelected] = useState<AdminOperation>(),
    [refund, setRefund] = useState<AdminOperation>(),
    [investigate, setInvestigate] = useState(false);
  const { data, error, reload } = useAdminPage<AdminOperation>(
    "transactions?" +
      query({
        q,
        cursor,
        account_id: account?.id,
        operation_type: type,
        from_date: from ? from + "T00:00:00Z" : "",
        to_date: to ? to + "T23:59:59Z" : "",
        limit: 20,
      }),
  );
  return (
    <>
      {account && (
        <p>
          {ui("История: ")}
          <b>{account.name}</b> · {account.public_number}
        </p>
      )}
      <div className="admin-toolbar">
        <input
          aria-label={ui("Поиск транзакций")}
          placeholder={ui("Номер операции, реквизиты или комментарий")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setCursor("");
          }}
        />
        <select
          aria-label={ui("Тип операции")}
          value={type}
          onChange={(e) => {
            setType(e.target.value);
            setCursor("");
          }}
        >
          <option value="">{ui("Все операции")}</option>
          {[
            "transfer",
            "payment",
            "refund",
            "issue",
            "hold",
            "capture_hold",
            "release_hold",
          ].map((x) => (
            <option value={x} key={x}>
              {operationName(x, (ru) => ru)}
            </option>
          ))}
        </select>
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
                  <th scope="col">{ui("Операция")}</th>
                  <th scope="col">{ui("Счета")}</th>
                  <th scope="col">{ui("Сумма")}</th>
                  <th scope="col">{ui("Дата")}</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((o) => (
                  <tr key={o.id}>
                    <td data-label={ui("Операция")}>
                      <b>{operationName(o.type, (ru) => ru)}</b>
                      <small>
                        {o.status === "completed"
                          ? ui("Завершена")
                          : o.status === "failed"
                            ? ui("Отклонена")
                            : ui("Отменена")}
                      </small>
                    </td>
                    <td data-label={ui("Счета")}>
                      {o.source_name || "—"} → {o.destination_name || "—"}
                      <small>{o.destination_public_number}</small>
                    </td>
                    <td data-label={ui("Сумма")}>
                      {money(o.amount, undefined)}
                    </td>
                    <td data-label={ui("Дата")}>
                      {dateTime(o.created_at, undefined)}
                    </td>
                    <td data-label={ui("Действия")}>
                      <button
                        className="secondary"
                        onClick={() => setSelected(o)}
                      >
                        {ui("Подробности")}
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
      {selected && !refund && !investigate && (
        <Modal
          title={ui("Подробности транзакции")}
          onClose={() => setSelected(undefined)}
        >
          <h3>{money(selected.amount, undefined)}</h3>
          <p>
            {selected.source_name} → {selected.destination_name}
          </p>
          <p>
            {selected.source_public_number} →{" "}
            {selected.destination_public_number}
          </p>
          <p>{selected.reason || ui("Без комментария")}</p>
          <p className="muted">#{selected.id}</p>
          <div className="admin-toolbar">
            {permissions.includes("admin.refunds") &&
              ["transfer", "payment"].includes(selected.type) &&
              selected.status === "completed" && (
                <button onClick={() => setRefund(selected)}>
                  {ui("Оформить возврат")}
                </button>
              )}
            {permissions.includes("admin.investigations") && (
              <button
                className="secondary"
                onClick={() => setInvestigate(true)}
              >
                {ui("Открыть расследование")}
              </button>
            )}
          </div>
        </Modal>
      )}
      {refund && (
        <MoneyAction
          operation={refund}
          onClose={() => setRefund(undefined)}
          onDone={reload}
        />
      )}
      {investigate && selected && (
        <ActionDialog
          title={ui("Открыть расследование?")}
          onClose={() => setInvestigate(false)}
          onConfirm={async (note) => {
            await api.createInvestigation("operation", selected.id, note);
            setSelected(undefined);
          }}
        >
          {" "}
          <p>
            {ui("Операция #")}
            {selected.id}
          </p>
        </ActionDialog>
      )}
    </>
  );
}
export function Finances({ permissions }: { permissions: string[] }) {
  useLocale();
  const [account, setAccount] = useState<AdminAccount>(),
    [issuing, setIssuing] = useState(false),
    [user, setUser] = useState<AdminUser>(),
    [coreRights, setCoreRights] = useState<{
      authorities: string[];
      account_permissions: {
        id: string;
        name: string;
        public_number: string;
        permission: string;
      }[];
    }>(),
    [cursor, setCursor] = useState("");
  const [permission, setPermission] = useState("admin.read"),
    [scope, setScope] = useState<AdminAccount>(),
    [revoke, setRevoke] = useState(false),
    [changing, setChanging] = useState(false),
    [error, setError] = useState("");
  const rightsRequest = useRef(0);
  async function loadRights(target: AdminUser, c = "") {
    const version = ++rightsRequest.current;
    setUser(target);
    setCursor(c);
    setError("");
    setCoreRights(undefined);
    try {
      const value = await adminApi.get<NonNullable<typeof coreRights>>(
        "financial-permissions?" + query({ user_id: target.id, cursor: c }),
      );
      if (version === rightsRequest.current) setCoreRights(value);
    } catch (e) {
      if (version === rightsRequest.current) setError(localError(e));
    }
  }
  return (
    <>
      <div className="panel">
        <h3>{ui("Финансовые полномочия")}</h3>
        <p>
          {ui(
            "Эмиссия и возвраты требуют глобальных полномочий ядра и разрешения на конкретный счёт. Проверки выполняются C++-ядром.",
          )}
        </p>
      </div>
      {permissions.includes("admin.issuance") && (
        <section className="panel">
          <h3>{ui("Эмиссия")}</h3>
          <AccountPicker onPick={setAccount} />
          {account && (
            <div className="delivery-row">
              <span>
                <b>
                  {account.name} · {account.owner}
                </b>
                <small>{account.public_number}</small>
              </span>
              <button onClick={() => setIssuing(true)}>
                {ui("Зачислить")}
              </button>
            </div>
          )}
        </section>
      )}
      {permissions.includes("admin.finance.manage") && (
        <section className="panel">
          <h3>{ui("Разрешения C++-ядра")}</h3>
          <UserPicker onPick={(u) => void loadRights(u)} />
          {user && (
            <>
              <h4>
                {user.display_name} · @{user.bank_username}
              </h4>
              {error && <p role="alert">{error}</p>}
              {coreRights && (
                <>
                  <div className="permission-tags">
                    {coreRights.authorities.map((p) => (
                      <span className="tag" key={p}>
                        {p}
                      </span>
                    ))}
                  </div>
                  {coreRights.account_permissions.map((p) => (
                    <div className="delivery-row" key={p.id}>
                      <span>
                        {p.name}
                        <small>{p.public_number}</small>
                      </span>
                      <code>{p.permission}</code>
                    </div>
                  ))}
                  <div className="admin-pagination">
                    <button
                      className="secondary"
                      disabled={!cursor}
                      onClick={() => void loadRights(user)}
                    >
                      {ui("В начало")}
                    </button>
                    <button
                      className="secondary"
                      disabled={coreRights.account_permissions.length < 25}
                      onClick={() =>
                        void loadRights(
                          user,
                          coreRights.account_permissions.at(-1)!.id,
                        )
                      }
                    >
                      {ui("Следующая страница")}
                    </button>
                  </div>
                </>
              )}
              <label>
                {ui("Область")}
                <select
                  value={scope ? "account" : "global"}
                  onChange={(e) => {
                    if (e.target.value === "global") {
                      setScope(undefined);
                      setPermission("admin.read");
                    } else {
                      setScope({
                        id: "",
                        name: ui("Выберите счёт"),
                      } as AdminAccount);
                      setPermission("accounts.read");
                    }
                  }}
                >
                  <option value="global">{ui("Глобальные полномочия")}</option>
                  <option value="account">{ui("Конкретный счёт")}</option>
                </select>
              </label>
              {scope && (
                <>
                  <AccountPicker onPick={(a) => setScope(a)} />
                  <p>
                    {scope.name} · {scope.public_number}
                  </p>
                </>
              )}
              <label>
                {ui("Разрешение")}
                <select
                  value={permission}
                  onChange={(e) => setPermission(e.target.value)}
                >
                  {(scope
                    ? [
                        "accounts.read",
                        "balance.read",
                        "history.read",
                        "transfer",
                        "payment.create",
                        "hold.create",
                        "refund.create",
                        "issue.create",
                        "account.manage",
                      ]
                    : [
                        "admin.read",
                        "issue.create",
                        "refund.create",
                        "authority.manage",
                        "account.state.manage",
                        "exchange.manage",
                      ]
                  ).map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={revoke}
                  onChange={(e) => setRevoke(e.target.checked)}
                />
                {ui("Отозвать разрешение")}
              </label>
              <button
                disabled={!!scope && !scope.id}
                onClick={() => setChanging(true)}
              >
                {ui("Проверить изменение")}
              </button>
            </>
          )}
        </section>
      )}
      {issuing && account && (
        <MoneyAction
          account={account}
          onClose={() => setIssuing(false)}
          onDone={() => undefined}
        />
      )}
      {changing && user && (
        <ActionDialog
          title={
            revoke
              ? ui("Отозвать финансовое право?")
              : ui("Выдать финансовое право?")
          }
          onClose={() => setChanging(false)}
          onConfirm={async (reason) => {
            await adminApi.write("financial-permissions", {
              user_id: user.id,
              account_id: scope?.id || null,
              permission,
              revoke,
              reason,
              confirmed: true,
            });
            await loadRights(user);
          }}
        >
          <p>
            {user.display_name} · @{user.bank_username}
          </p>
          <p>
            {permission} ·{" "}
            {scope
              ? scope.name + " " + scope.public_number
              : ui("Глобальное полномочие")}
          </p>
          <p>
            {ui(
              "Ядро проверит ваше право делегировать это разрешение. Изменение собственных прав запрещено.",
            )}
          </p>
        </ActionDialog>
      )}
      {permissions.includes("admin.transactions.read") && (
        <>
          <h3>{ui("История эмиссий")}</h3>
          <Transactions permissions={permissions} issuances />
        </>
      )}
    </>
  );
}
