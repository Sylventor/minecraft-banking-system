import {
  localize as ui,
  localizeTemplate as uit,
  useI18n as useLocale,
} from "../../i18n";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  businessApi,
  type BusinessPage,
  type Employee,
  type PayrollBatch,
  type PayrollItem,
  type PayrollSchedule,
} from "../../business-api";
import { useBank } from "../../bank-context";
import {
  Empty,
  Icon,
  Loading,
  Modal,
  PageHeading,
} from "../../components/BankUI";
import { Disclosure, Steps } from "../../components/Flow";
import { Notice } from "../../components/Notice";
import { RecipientPicker } from "../../components/RecipientPicker";
import { dateTime, localError, money, normalizeAmount } from "../../i18n";
import type { Organization, Recipient } from "../../types";
import { cents } from "./Analytics";
const states = (): Record<string, string> => ({
  draft: ui("Черновик"),
  pending_approval: ui("Ждёт утверждения"),
  processing: ui("Выполняется"),
  completed: ui("Выполнено"),
  partially_completed: ui("Частично выполнено"),
  failed: ui("Ошибка"),
  cancelled: ui("Отменено"),
  pending: ui("Ожидает"),
  active: ui("Активно"),
  paused: ui("Приостановлено"),
  disabled: ui("Отключено"),
});
const frequencies = (): Record<string, string> => ({
  weekly: ui("Еженедельно"),
  biweekly: ui("Раз в две недели"),
  monthly: ui("Ежемесячно"),
});
type Overview = {
  employees: number;
  paid_last30: string;
  last_batch: {
    id: string;
    status: string;
    total: string;
    created_at: string;
  } | null;
  next_schedule: {
    name: string;
    next_run_at: string;
    total: string;
    mode: string;
  } | null;
};
type Confirmation = {
  kind: "batch" | "schedule" | "employee";
  id: string;
  action: string;
  name: string;
  revision?: number;
  schedule?: PayrollSchedule;
};
function amountText(value: bigint) {
  return `${value / 100n}.${(value % 100n).toString().padStart(2, "0")}`;
}
export function Payroll({
  organization,
  permissions,
}: {
  organization: Organization;
  permissions: string[];
}) {
  useLocale();
  const { data, refresh, openReceipt } = useBank();
  const org = organization.id;
  const [tab, setTab] = useState("overview"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<Overview>(),
    [employees, setEmployees] = useState<BusinessPage<Employee>>(),
    [batches, setBatches] = useState<BusinessPage<PayrollBatch>>(),
    [schedules, setSchedules] = useState<BusinessPage<PayrollSchedule>>(),
    [history, setHistory] = useState<BusinessPage<PayrollItem>>();
  const [cursor, setCursor] = useState(""),
    [previous, setPrevious] = useState<string[]>([]),
    [search, setSearch] = useState(""),
    [active, setActive] = useState("true");
  const [editor, setEditor] = useState<"employee" | "batch" | "schedule">(),
    [editingEmployee, setEditingEmployee] = useState<Employee>(),
    [editingSchedule, setEditingSchedule] = useState<PayrollSchedule>();
  const [recipient, setRecipient] = useState<Recipient>(),
    [salary, setSalary] = useState(""),
    [position, setPosition] = useState(""),
    [note, setNote] = useState(""),
    [employeeFrequency, setEmployeeFrequency] = useState("monthly");
  const [selected, setSelected] = useState<Record<string, string>>({}),
    [source, setSource] = useState(""),
    [description, setDescription] = useState(ui("Зарплата")),
    [name, setName] = useState("");
  const [frequency, setFrequency] = useState("monthly"),
    [weekday, setWeekday] = useState("1"),
    [monthday, setMonthday] = useState("1"),
    [clock, setClock] = useState("10:00"),
    [timezone, setTimezone] = useState(
      Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    ),
    [mode, setMode] = useState("manual"),
    [cap, setCap] = useState(""),
    [consent, setConsent] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>(),
    [detail, setDetail] = useState<PayrollBatch>();
  const [choices, setChoices] = useState<BusinessPage<Employee>>();
  const [batchStep, setBatchStep] = useState(0);
  const attempt = useRef<
      | {
          payload: string;
          key: string;
        }
      | undefined
    >(undefined),
    sequence = useRef(0);
  const has = (p: string) => permissions.includes(p);
  const load = useCallback(async () => {
    const n = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const suffix = new URLSearchParams({
        limit: "100",
        ...(cursor ? { cursor } : {}),
      });
      const overviewValue = await businessApi.get<Overview>(org, "overview");
      const list =
        tab === "employees"
          ? await businessApi.get<BusinessPage<Employee>>(
              org,
              `employees?${suffix}&q=${encodeURIComponent(search)}${active ? `&active=${active}` : ""}`,
            )
          : null;
      const b =
        tab === "batches"
          ? await businessApi.get<BusinessPage<PayrollBatch>>(
              org,
              "batches?" + suffix,
            )
          : null;
      const s =
        tab === "schedules"
          ? await businessApi.get<BusinessPage<PayrollSchedule>>(
              org,
              "schedules?" + suffix,
            )
          : null;
      const h =
        tab === "history"
          ? await businessApi.get<BusinessPage<PayrollItem>>(
              org,
              "history?" + suffix,
            )
          : null;
      if (n === sequence.current) {
        setOverview(overviewValue);
        if (list) setEmployees(list);
        if (b) setBatches(b);
        if (s) setSchedules(s);
        if (h) setHistory(h);
      }
    } catch (e) {
      if (n === sequence.current) setError(localError(e));
    } finally {
      if (n === sequence.current) setLoading(false);
    }
  }, [org, tab, cursor, search, active]);
  useEffect(() => {
    void load();
  }, [load]);
  const sources = (organization.accounts || []).filter(
    (a) => a.status === "active",
  );
  const total = Object.values(selected).reduce((sum, v) => {
    const normalized = normalizeAmount(v);
    return sum + (normalized ? cents(normalized) : 0n);
  }, 0n);
  async function openEditor(
    kind: "employee" | "batch" | "schedule",
    employee?: Employee,
    schedule?: PayrollSchedule,
  ) {
    setError("");
    setEditingEmployee(employee);
    setEditingSchedule(schedule);
    setRecipient(undefined);
    setConsent(false);
    setBatchStep(0);
    if (kind === "employee") {
      setSalary(employee?.salary || "");
      setPosition(employee?.position || "");
      setNote(employee?.note || "");
      setEmployeeFrequency(employee?.frequency || "monthly");
    } else {
      try {
        const list = await businessApi.get<BusinessPage<Employee>>(
          org,
          "employees?limit=100&active=true",
        );
        setChoices(list);
        setSelected(
          schedule
            ? Object.fromEntries(
                schedule.items.map((i) => [i.employee_id, i.amount]),
              )
            : {},
        );
        setSource(schedule?.source_account_id || sources[0]?.id || "");
        setDescription(schedule?.name || ui("Зарплата"));
        setName(schedule?.name || "");
        setFrequency(schedule?.frequency || "monthly");
        setWeekday(String(schedule?.weekday || 1));
        setMonthday(String(schedule?.month_day || 1));
        setClock(schedule?.local_time.slice(0, 5) || "10:00");
        setTimezone(
          schedule?.timezone ||
            Intl.DateTimeFormat().resolvedOptions().timeZone ||
            "UTC",
        );
        setMode(schedule?.mode || "manual");
        setCap(schedule?.max_amount || "");
      } catch (e) {
        setError(localError(e));
        return;
      }
    }
    setEditor(kind);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (editor === "batch") {
      const values = Object.values(selected);
      if (
        !values.length ||
        values.length > 100 ||
        values.some((value) => !normalizeAmount(value))
      ) {
        setError(
          ui("Выберите от 1 до 100 сотрудников и проверьте суммы выплат."),
        );
        return;
      }
      if (batchStep >= 2 && !sources.some((a) => a.id === source)) {
        setError(ui("Выберите действующий счёт организации."));
        return;
      }
      if (batchStep < 4) {
        setError("");
        setBatchStep((v) => v + 1);
        return;
      }
      if (!consent) {
        setError(ui("Подтвердите состав и сумму ведомости."));
        return;
      }
    }
    setBusy(true);
    setError("");
    try {
      if (editor === "employee") {
        const canonical = normalizeAmount(salary);
        if (!canonical) throw new Error(ui("Введите корректную зарплату"));
        if (editingEmployee) {
          if (recipient && recipient.id !== editingEmployee.user_id)
            throw new Error(
              ui("Счёт должен принадлежать выбранному сотруднику"),
            );
          await businessApi.write(
            org,
            `employees/${editingEmployee.id}`,
            {
              salary: canonical,
              frequency: employeeFrequency,
              position,
              note,
              ...(recipient ? { account_id: recipient.account_id } : {}),
            },
            "PATCH",
          );
        } else {
          if (!recipient?.id)
            throw new Error(ui("Выберите сотрудника и его личный счёт"));
          await businessApi.write(org, "employees", {
            user_id: recipient.id,
            account_id: recipient.account_id,
            salary: canonical,
            frequency: employeeFrequency,
            position,
            note,
          });
        }
      } else {
        const items = Object.entries(selected).map(([employee_id, v]) => {
          const amount = normalizeAmount(v);
          if (!amount) throw new Error(ui("Проверьте суммы выплат"));
          return { employee_id, amount };
        });
        if (!items.length) throw new Error(ui("Выберите сотрудников"));
        const body = { source_account_id: source, description, items };
        if (editor === "batch") {
          const payload = JSON.stringify(body);
          if (attempt.current?.payload !== payload)
            attempt.current = { payload, key: crypto.randomUUID() };
          const b = await businessApi.write<PayrollBatch>(
            org,
            "batches",
            body,
            "POST",
            attempt.current.key,
          );
          setDetail(b);
          attempt.current = undefined;
        } else {
          const max_amount = normalizeAmount(cap);
          if (!max_amount || !consent)
            throw new Error(ui("Укажите лимит и подтвердите режим расписания"));
          await businessApi.write(
            org,
            editingSchedule ? `schedules/${editingSchedule.id}` : "schedules",
            {
              ...body,
              name,
              frequency,
              weekday: Number(weekday),
              month_day: Number(monthday),
              local_time: clock,
              timezone,
              mode,
              max_amount,
              confirmed: true,
            },
            editingSchedule ? "PATCH" : "POST",
          );
        }
      }
      setEditor(undefined);
      await load();
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error && !("code" in e) ? e.message : localError(e),
      );
    } finally {
      setBusy(false);
    }
  }
  async function act() {
    if (!confirmation) return;
    setBusy(true);
    setError("");
    try {
      const c = confirmation;
      if (c.kind === "batch") {
        setDetail(
          await businessApi.write<PayrollBatch>(
            org,
            `batches/${c.id}/${c.action}`,
            { confirmed: true },
          ),
        );
      } else if (c.kind === "schedule") {
        if (c.action === "approve")
          await businessApi.write(org, `schedules/${c.id}/approve`, {
            confirmed: true,
            revision: c.revision,
          });
        else
          await businessApi.write(
            org,
            `schedules/${c.id}`,
            { status: c.action },
            "PATCH",
          );
      } else
        await businessApi.write(
          org,
          `employees/${c.id}`,
          { active: c.action === "enable" },
          "PATCH",
        );
      setConfirmation(undefined);
      await load();
      await refresh();
    } catch (e) {
      setError(
        confirmation.kind === "batch" &&
          ["execute", "retry"].includes(confirmation.action)
          ? ui(
              "Ответ не получен. Сначала проверьте результат ведомости по её ID; не создавайте новую выплату. ",
            ) + localError(e)
          : localError(e),
      );
    } finally {
      setBusy(false);
    }
  }
  function switchTab(value: string) {
    setTab(value);
    setCursor("");
    setPrevious([]);
  }
  const next =
    tab === "employees"
      ? employees?.next_cursor
      : tab === "batches"
        ? batches?.next_cursor
        : tab === "schedules"
          ? schedules?.next_cursor
          : history?.next_cursor;
  const labels: Record<string, string> = {
    submit: ui("Отправить на утверждение"),
    approve: ui("Утвердить"),
    execute: ui("Выплатить"),
    retry: ui("Повторить неуспешные"),
    cancel: ui("Отменить"),
    active: ui("Возобновить"),
    paused: ui("Приостановить"),
    disabled: ui("Отключить"),
    disable: ui("Отключить сотрудника"),
    enable: ui("Включить сотрудника"),
  };
  return (
    <>
      <PageHeading
        title={ui("Зарплаты")}
        subtitle={ui(
          "Выплаты с реального счёта организации. Создание ведомости не создаёт деньги.",
        )}
      />
      <Notice error={error} />
      <div className="payment-tabs">
        {Object.entries({
          overview: ui("Обзор"),
          employees: ui("Сотрудники"),
          batches: ui("Ведомости"),
          schedules: ui("Расписания"),
          history: ui("История выплат"),
        }).map(([v, t]) => (
          <button
            key={v}
            className={tab === v ? "" : "secondary"}
            onClick={() => switchTab(v)}
          >
            {t}
          </button>
        ))}
      </div>
      {loading && !overview ? (
        <Loading />
      ) : (
        <>
          {tab === "overview" && overview && (
            <div className="business-metrics">
              <section className="panel">
                <span>{ui("Активные сотрудники")}</span>
                <h2>{overview.employees}</h2>
              </section>
              <section className="panel">
                <span>{ui("Выплачено за 30 дней")}</span>
                <h2>{money(overview.paid_last30)}</h2>
              </section>
              <section className="panel">
                <span>{ui("Ближайшее расписание")}</span>
                <h3>{overview.next_schedule?.name || ui("Не настроено")}</h3>
                {overview.next_schedule && (
                  <>
                    <p>
                      {dateTime(overview.next_schedule.next_run_at)} ·{" "}
                      {money(overview.next_schedule.total)}
                    </p>
                    <small>
                      {overview.next_schedule.mode === "manual"
                        ? ui("Потребуется ручное утверждение")
                        : ui("По утверждённому мандату")}
                    </small>
                  </>
                )}
              </section>
              <section className="panel">
                <span>{ui("Последняя ведомость")}</span>
                <h3>
                  {overview.last_batch
                    ? states()[overview.last_batch.status]
                    : ui("Нет ведомостей")}
                </h3>
                {overview.last_batch && (
                  <p>
                    {money(overview.last_batch.total)} ·{" "}
                    {dateTime(overview.last_batch.created_at)}
                  </p>
                )}
              </section>
            </div>
          )}
          {tab === "employees" && (
            <section className="panel">
              <div className="section-heading">
                <h2>{ui("Зарплатные сотрудники")}</h2>
                {has("payroll.employees.manage") &&
                  has("payroll.salaries.manage") && (
                    <button onClick={() => void openEditor("employee")}>
                      {ui("Добавить сотрудника")}
                    </button>
                  )}
              </div>
              <div className="business-filters">
                <label>
                  {ui("Поиск")}
                  <input
                    value={search}
                    onChange={(e) => {
                      setSearch(e.target.value);
                      setCursor("");
                      setPrevious([]);
                    }}
                  />
                </label>
                <label>
                  {ui("Статус")}
                  <select
                    value={active}
                    onChange={(e) => {
                      setActive(e.target.value);
                      setCursor("");
                      setPrevious([]);
                    }}
                  >
                    <option value="true">{ui("Активные")}</option>
                    <option value="false">{ui("Отключённые")}</option>
                    <option value="">{ui("Все")}</option>
                  </select>
                </label>
              </div>
              <div className="business-table">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{ui("Сотрудник")}</th>
                      <th scope="col">{ui("Должность")}</th>
                      <th scope="col">{ui("Зарплата")}</th>
                      <th scope="col">{ui("Периодичность")}</th>
                      <th scope="col">{ui("Статус")}</th>
                      <th scope="col">{ui("Действия")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {employees?.items.map((e) => (
                      <tr key={e.id}>
                        <td data-label={ui("Сотрудник")}>{e.user_name}</td>
                        <td data-label={ui("Должность")}>
                          {e.position || "—"}
                        </td>
                        <td data-label={ui("Зарплата")}>{money(e.salary)}</td>
                        <td data-label={ui("Периодичность")}>
                          {frequencies()[e.frequency]}
                        </td>
                        <td data-label={ui("Статус")}>
                          {e.active ? ui("Активен") : ui("Отключён")}
                        </td>
                        <td data-label={ui("Действия")}>
                          {has("payroll.employees.manage") && (
                            <>
                              {has("payroll.salaries.manage") &&
                                e.user_id !== data.profile.id && (
                                  <button
                                    className="text-button"
                                    onClick={() =>
                                      void openEditor("employee", e)
                                    }
                                  >
                                    {ui("Изменить")}
                                  </button>
                                )}
                              <button
                                className="text-button"
                                onClick={() =>
                                  setConfirmation({
                                    kind: "employee",
                                    id: e.id,
                                    action: e.active ? "disable" : "enable",
                                    name: e.user_name,
                                  })
                                }
                              >
                                {e.active ? ui("Отключить") : ui("Включить")}
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {tab === "batches" && (
            <section className="panel">
              <div className="section-heading">
                <h2>{ui("Ведомости")}</h2>
                {has("payroll.batches.create") && (
                  <button onClick={() => void openEditor("batch")}>
                    {ui("Создать ведомость")}
                  </button>
                )}
              </div>
              {!batches?.items.length ? (
                <Empty
                  icon="history"
                  title={ui("Нет ведомостей")}
                  description={ui(
                    "Выберите сотрудников и сформируйте первый черновик.",
                  )}
                />
              ) : (
                <div className="business-list">
                  {batches.items.map((b) => (
                    <button
                      className="secondary business-list-row"
                      key={b.id}
                      onClick={() => setDetail(b)}
                    >
                      <span>
                        {b.description}
                        <small>{dateTime(b.created_at)}</small>
                      </span>
                      <strong>{money(b.total)}</strong>
                      <span className="tag">{states()[b.status]}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          )}
          {tab === "schedules" && (
            <section className="panel">
              <div className="section-heading">
                <h2>{ui("Регулярные выплаты")}</h2>
                {has("payroll.automatic.manage") &&
                  has("payroll.batches.create") && (
                    <button onClick={() => void openEditor("schedule")}>
                      {ui("Создать расписание")}
                    </button>
                  )}
              </div>
              <p>
                {ui(
                  "Владелец организации может утвердить автоматические выплаты сам. Для остальных нужен другой уполномоченный сотрудник, который не получает выплаты. Изменения зарплат и реквизитов снимают утверждение.",
                )}
              </p>
              <div className="business-list">
                {schedules?.items.map((s) => (
                  <article className="business-schedule" key={s.id}>
                    <h3>{s.name}</h3>
                    <p className="schedule-date">
                      <Icon name="clock" />
                      {ui("Следующая выплата: ")}
                      {dateTime(s.next_run_at)} · {s.timezone}
                    </p>
                    <p>
                      {frequencies()[s.frequency]} · {s.local_time} ·{" "}
                      {s.timezone}
                    </p>
                    <p>
                      {s.mode === "automatic"
                        ? ui("Автоматически")
                        : ui("С ручным утверждением")}{" "}
                      · {states()[s.status]} · {dateTime(s.next_run_at)}
                    </p>
                    <p>
                      {ui("Лимит ")}
                      {money(s.max_amount)} · {s.items.length}
                      {ui(" сотрудников")}
                    </p>
                    {s.last_error && (
                      <p role="status">
                        {ui("Требуется проверка:")}{" "}
                        {s.last_error === "employee_changed"
                          ? ui(
                              "Изменились сотрудники. Обновите снимок и утвердите его повторно.",
                            )
                          : ui("Проверьте баланс, реквизиты и полномочия.")}
                      </p>
                    )}
                    {has("payroll.automatic.manage") &&
                      s.status !== "disabled" && (
                        <div className="payment-tabs">
                          <button
                            className="secondary"
                            onClick={() =>
                              void openEditor("schedule", undefined, s)
                            }
                          >
                            {ui("Изменить / обновить снимок")}
                          </button>
                          {s.mode === "automatic" &&
                            !s.approved_by &&
                            has("payroll.approve") && (
                              <button
                                onClick={() =>
                                  setConfirmation({
                                    kind: "schedule",
                                    id: s.id,
                                    action: "approve",
                                    name: s.name,
                                    revision: s.revision,
                                    schedule: s,
                                  })
                                }
                              >
                                {ui("Утвердить автоматические выплаты")}
                              </button>
                            )}
                          <button
                            className="secondary"
                            onClick={() =>
                              setConfirmation({
                                kind: "schedule",
                                id: s.id,
                                action:
                                  s.status === "active" ? "paused" : "active",
                                name: s.name,
                              })
                            }
                          >
                            {s.status === "active"
                              ? ui("Приостановить")
                              : ui("Возобновить")}
                          </button>
                          <button
                            className="text-button danger"
                            onClick={() =>
                              setConfirmation({
                                kind: "schedule",
                                id: s.id,
                                action: "disabled",
                                name: s.name,
                              })
                            }
                          >
                            {ui("Отключить")}
                          </button>
                        </div>
                      )}
                  </article>
                ))}
              </div>
            </section>
          )}
          {tab === "history" && (
            <section className="panel">
              <h2>{ui("История отдельных выплат")}</h2>
              <div className="business-table">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{ui("Сотрудник")}</th>
                      <th scope="col">{ui("Сумма")}</th>
                      <th scope="col">{ui("Статус")}</th>
                      <th scope="col">{ui("Дата")}</th>
                      <th scope="col">{ui("Результат")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history?.items.map((i) => (
                      <tr key={i.id}>
                        <td data-label={ui("Сотрудник")}>{i.user_name}</td>
                        <td data-label={ui("Сумма")}>{money(i.amount)}</td>
                        <td data-label={ui("Статус")}>{states()[i.status]}</td>
                        <td data-label={ui("Дата")}>
                          {i.paid_at ? dateTime(i.paid_at) : "—"}
                        </td>
                        <td data-label={ui("Результат")}>
                          {i.operation_id ? (
                            <code>{i.operation_id}</code>
                          ) : (
                            i.error_code || "—"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {has("analytics.export") && (
                <button
                  className="secondary"
                  onClick={() => {
                    businessApi
                      .export(
                        org,
                        new URLSearchParams({
                          export_type: "payroll",
                          period: "30d",
                          limit: "100",
                        }),
                      )
                      .catch((e) => setError(e.message));
                  }}
                >
                  {ui("Экспорт выплат за 30 дней CSV")}
                </button>
              )}
            </section>
          )}
          {tab !== "overview" && (
            <div className="payment-pagination">
              <button
                className="secondary"
                disabled={!previous.length || loading}
                onClick={() => {
                  setCursor(previous.at(-1) || "");
                  setPrevious((v) => v.slice(0, -1));
                }}
              >
                {ui("Назад")}
              </button>
              <button
                className="secondary"
                disabled={!next || loading}
                onClick={() => {
                  setPrevious((v) => [...v, cursor]);
                  setCursor(next || "");
                }}
              >
                {ui("Далее")}
              </button>
              <button
                className="secondary"
                disabled={loading}
                onClick={() => void load()}
              >
                {ui("Обновить")}
              </button>
            </div>
          )}
        </>
      )}
      {editor && (
        <Modal
          title={
            editor === "employee"
              ? editingEmployee
                ? ui("Изменить зарплатного сотрудника")
                : ui("Новый зарплатный сотрудник")
              : editor === "batch"
                ? ui("Новая ведомость")
                : ui("Расписание зарплат")
          }
          onClose={() => {
            if (!busy) setEditor(undefined);
          }}
        >
          <Notice error={error} />
          {editor === "batch" && (
            <Steps
              labels={[
                ui("Сотрудники"),
                ui("Суммы"),
                ui("Счёт"),
                ui("Обзор"),
                ui("Подтверждение"),
              ]}
              current={batchStep}
            />
          )}
          <form onSubmit={(e) => void save(e)}>
            {editor === "employee" ? (
              <>
                {editingEmployee && <p>{editingEmployee.user_name}</p>}
                <p>
                  {ui(
                    "Выберите человека или найдите другой его личный счёт по реквизитам HB-. Участие в управлении организацией не требуется.",
                  )}
                </p>
                <RecipientPicker value={recipient} onChange={setRecipient} />
                <label>
                  {ui("Зарплата, ДБ")}
                  <input
                    value={salary}
                    inputMode="decimal"
                    onChange={(e) => setSalary(e.target.value)}
                    required
                  />
                </label>
                <label>
                  {ui("Периодичность")}
                  <select
                    value={employeeFrequency}
                    onChange={(e) => setEmployeeFrequency(e.target.value)}
                  >
                    {Object.entries(frequencies()).map(([v, t]) => (
                      <option key={v} value={v}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {ui("Должность")}
                  <input
                    value={position}
                    maxLength={100}
                    onChange={(e) => setPosition(e.target.value)}
                  />
                </label>
                <Disclosure title={ui("Дополнительное примечание")}>
                  <label>
                    {ui("Примечание")}
                    <input
                      value={note}
                      maxLength={500}
                      onChange={(e) => setNote(e.target.value)}
                    />
                  </label>
                </Disclosure>
              </>
            ) : (
              <>
                {(editor !== "batch" || batchStep === 2) && (
                  <>
                    <label>
                      {ui("Счёт организации")}
                      <select
                        value={source}
                        onChange={(e) => setSource(e.target.value)}
                        required
                      >
                        <option value="">{ui("Выберите счёт")}</option>
                        {sources.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} · {a.public_number}
                            {ui(" · доступно")} {money(a.available.amount)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {ui("Назначение")}
                      <input
                        value={description}
                        maxLength={450}
                        onChange={(e) => setDescription(e.target.value)}
                        required
                      />
                    </label>
                  </>
                )}
                {(editor !== "batch" || batchStep <= 1) && (
                  <fieldset>
                    <legend>{ui("Сотрудники (до 100 в ведомости)")}</legend>
                    {choices?.items
                      .filter(
                        (e) =>
                          e.active &&
                          (editor !== "batch" ||
                            batchStep !== 1 ||
                            e.id in selected),
                      )
                      .map((e) => (
                        <div className="business-employee-choice" key={e.id}>
                          <label className="checkbox">
                            <input
                              type="checkbox"
                              disabled={editor === "batch" && batchStep === 1}
                              checked={e.id in selected}
                              onChange={(event) =>
                                setSelected((v) => {
                                  const next = { ...v };
                                  if (event.target.checked)
                                    next[e.id] = e.salary;
                                  else delete next[e.id];
                                  return next;
                                })
                              }
                            />
                            {e.user_name} · {e.position}
                          </label>
                          {e.id in selected &&
                            (editor !== "batch" || batchStep === 1) && (
                              <label>
                                {ui("Выплата, ДБ")}
                                <input
                                  aria-label={uit("Выплата {0}", e.user_name)}
                                  value={selected[e.id]}
                                  inputMode="decimal"
                                  disabled={
                                    !has("payroll.salaries.manage") ||
                                    e.user_id === data.profile.id
                                  }
                                  onChange={(event) =>
                                    setSelected((v) => ({
                                      ...v,
                                      [e.id]: event.target.value,
                                    }))
                                  }
                                />
                              </label>
                            )}
                        </div>
                      ))}
                    {choices?.next_cursor && (
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            const page = await businessApi.get<
                              BusinessPage<Employee>
                            >(
                              org,
                              "employees?limit=100&active=true&cursor=" +
                                choices.next_cursor,
                            );
                            setChoices({
                              items: [...choices.items, ...page.items],
                              next_cursor: page.next_cursor,
                            });
                          } catch (e) {
                            setError(localError(e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        {ui("Загрузить следующих сотрудников")}
                      </button>
                    )}
                  </fieldset>
                )}
                {(editor !== "batch" || batchStep >= 3) && (
                  <div className="business-preview">
                    <p>
                      {ui("Счёт: ")}
                      {sources.find((a) => a.id === source)?.name} ·{" "}
                      {sources.find((a) => a.id === source)?.public_number}
                    </p>
                    <p>
                      {ui("Назначение: ")}
                      {description}
                    </p>
                    <p>
                      {ui("Сотрудников: ")}
                      {Object.keys(selected).length}
                    </p>
                    {Object.entries(selected).map(([id, value]) => (
                      <p key={id}>
                        {choices?.items.find((e) => e.id === id)?.user_name ||
                          editingSchedule?.items.find(
                            (i) => i.employee_id === id,
                          )?.user_name ||
                          id}
                        : {money(normalizeAmount(value) || "0.00")}
                        {(editor !== "batch" || batchStep === 3) && (
                          <button
                            type="button"
                            className="text-button"
                            onClick={() =>
                              setSelected((v) => {
                                const next = { ...v };
                                delete next[id];
                                return next;
                              })
                            }
                          >
                            {ui("Убрать")}
                          </button>
                        )}
                      </p>
                    ))}
                    <h3>
                      {ui("Итого ")}
                      {money(amountText(total))}
                    </h3>
                    <p>
                      {ui("Комиссия: 0,00 ДБ. К списанию: ")}
                      {money(amountText(total))}.
                    </p>
                    <small>
                      {ui(
                        "Это предварительный расчёт. Перед фактическим выполнением ядро повторно проверяет баланс и права.",
                      )}
                    </small>
                  </div>
                )}
                {editor === "batch" && batchStep === 4 && (
                  <>
                    <p className="info-box">
                      {ui(
                        "Сейчас создаётся черновик ведомости. Деньги будут списаны только после её отдельного утверждения и подтверждения выплаты.",
                      )}
                    </p>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={consent}
                        onChange={(e) => setConsent(e.target.checked)}
                        required
                      />
                      {ui("Я проверил сотрудников, суммы и счёт организации")}
                    </label>
                  </>
                )}
                {editor === "schedule" && (
                  <>
                    <label>
                      {ui("Название")}
                      <input
                        value={name}
                        maxLength={120}
                        onChange={(e) => setName(e.target.value)}
                        required
                      />
                    </label>
                    <label>
                      {ui("Период")}
                      <select
                        value={frequency}
                        onChange={(e) => setFrequency(e.target.value)}
                      >
                        {Object.entries(frequencies()).map(([v, t]) => (
                          <option key={v} value={v}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </label>
                    {frequency === "monthly" ? (
                      <label>
                        {ui("День месяца")}
                        <input
                          type="number"
                          min={1}
                          max={31}
                          value={monthday}
                          onChange={(e) => setMonthday(e.target.value)}
                          required
                        />
                      </label>
                    ) : (
                      <label>
                        {ui("День недели")}
                        <select
                          value={weekday}
                          onChange={(e) => setWeekday(e.target.value)}
                        >
                          {[
                            ui("Понедельник"),
                            ui("Вторник"),
                            ui("Среда"),
                            ui("Четверг"),
                            ui("Пятница"),
                            ui("Суббота"),
                            ui("Воскресенье"),
                          ].map((t, i) => (
                            <option key={t} value={i + 1}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    <label>
                      {ui("Время")}
                      <input
                        type="time"
                        value={clock}
                        onChange={(e) => setClock(e.target.value)}
                        required
                      />
                    </label>
                    <label>
                      {ui("Часовой пояс")}
                      <input
                        value={timezone}
                        maxLength={64}
                        onChange={(e) => setTimezone(e.target.value)}
                        required
                      />
                    </label>
                    <label>
                      {ui("Режим")}
                      <select
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                      >
                        <option value="manual">
                          {ui("Создать ведомость для ручного утверждения")}
                        </option>
                        <option value="automatic">
                          {ui(
                            "Автоматические выплаты по утверждённому мандату",
                          )}
                        </option>
                      </select>
                    </label>
                    <label>
                      {ui("Максимум одной ведомости, ДБ")}
                      <input
                        value={cap}
                        inputMode="decimal"
                        onChange={(e) => setCap(e.target.value)}
                        required
                      />
                    </label>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={consent}
                        onChange={(e) => setConsent(e.target.checked)}
                        required
                      />
                      {ui(
                        "Подтверждаю выбранный режим, получателей, реквизиты и лимит. Владелец организации может утвердить мандат сам; для остальных нужен другой уполномоченный сотрудник, который не получает выплаты.",
                      )}
                    </label>
                  </>
                )}
              </>
            )}
            <div className="flow-footer">
              {editor === "batch" && (
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || batchStep === 0}
                  onClick={() => {
                    setBatchStep((v) => v - 1);
                    setError("");
                  }}
                >
                  {ui("Назад")}
                </button>
              )}
              <button
                disabled={
                  busy ||
                  (editor === "batch" && batchStep === 4 && !consent) ||
                  (editor !== "employee" &&
                    editor !== "batch" &&
                    (!source || !Object.keys(selected).length))
                }
              >
                {busy
                  ? ui("Сохраняем…")
                  : editor === "batch"
                    ? batchStep < 4
                      ? ui("Продолжить")
                      : ui("Создать черновик")
                    : ui("Сохранить")}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {detail && !confirmation && !editor && (
        <Modal
          title={ui("Зарплатная ведомость")}
          onClose={() => {
            if (!busy) setDetail(undefined);
          }}
        >
          <Notice error={error} />
          <h2>{money(detail.total)}</h2>
          <p>
            {detail.description} · {states()[detail.status]}
          </p>
          <p>
            {ui("Комиссия ")}
            {money(detail.fee)}
            {ui(" · Сотрудников ")}
            {detail.items.length}
          </p>
          <div className="business-table">
            <table>
              <thead>
                <tr>
                  <th scope="col">{ui("Сотрудник")}</th>
                  <th scope="col">{ui("Выплата")}</th>
                  <th scope="col">{ui("Результат")}</th>
                </tr>
              </thead>
              <tbody>
                {detail.items.map((i) => (
                  <tr key={i.id}>
                    <td data-label={ui("Сотрудник")}>{i.user_name}</td>
                    <td data-label={ui("Выплата")}>{money(i.amount)}</td>
                    <td data-label={ui("Результат")}>
                      {states()[i.status]}{" "}
                      {i.error_code && <small>{i.error_code}</small>}
                      {i.operation_id && (
                        <button
                          className="text-button"
                          onClick={() => {
                            setDetail(undefined);
                            openReceipt(i.operation_id!);
                          }}
                        >
                          {ui("Квитанция")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="payment-tabs">
            {(detail.status === "draft"
              ? ["submit", "cancel"]
              : detail.status === "pending_approval"
                ? [detail.approved_by ? "execute" : "approve", "cancel"]
                : detail.status === "failed" ||
                    detail.status === "partially_completed"
                  ? ["retry"]
                  : []
            )
              .filter((act) =>
                has(
                  act === "approve"
                    ? "payroll.approve"
                    : act === "execute" || act === "retry"
                      ? "payroll.execute"
                      : "payroll.batches.create",
                ),
              )
              .map((act) => (
                <button
                  key={act}
                  className={act === "cancel" ? "secondary" : ""}
                  onClick={() =>
                    setConfirmation({
                      kind: "batch",
                      id: detail.id,
                      action: act,
                      name: `${detail.description} · ${money(detail.total)}`,
                    })
                  }
                >
                  {labels[act]}
                </button>
              ))}
            <button
              className="secondary"
              onClick={async () => {
                try {
                  setDetail(
                    await businessApi.get<PayrollBatch>(
                      org,
                      "batches/" + detail.id,
                    ),
                  );
                  await load();
                } catch (e) {
                  setError(localError(e));
                }
              }}
            >
              {ui("Проверить результат / обновить")}
            </button>
          </div>
          {detail.status === "processing" && (
            <p role="status">
              {ui(
                "Выполняется. Успех будет показан только после подтверждения ядра. Повтор не создаст второе списание.",
              )}
            </p>
          )}
        </Modal>
      )}
      {confirmation && (
        <Modal
          title={labels[confirmation.action] || ui("Подтвердите действие")}
          onClose={() => {
            if (!busy) setConfirmation(undefined);
          }}
        >
          <Notice error={error} />
          <h3>{confirmation.name}</h3>
          {confirmation.kind === "batch" && detail && (
            <div className="business-preview">
              <p>
                {ui("Счёт:")}{" "}
                {organization.accounts?.find(
                  (a) => a.id === detail.source_account_id,
                )?.public_number || detail.source_account_id}
              </p>
              <p>
                {ui("Итого ")}
                {money(detail.total)}
                {ui(" · комиссия ")}
                {money(detail.fee)}
              </p>
              {detail.items.map((i) => (
                <p key={i.id}>
                  {i.user_name} · {i.destination_account_id} · {money(i.amount)}{" "}
                  · {states()[i.status]}
                </p>
              ))}
            </div>
          )}
          {confirmation.kind === "batch" &&
            ["execute", "retry"].includes(confirmation.action) && (
              <p>
                {ui(
                  "Будут переведены только неисполненные выплаты этой ведомости. Счёт и суммы зафиксированы при её создании.",
                )}
              </p>
            )}
          {confirmation.kind === "schedule" &&
            confirmation.action === "approve" && (
              <div className="business-preview">
                <p>
                  {ui("Счёт списания:")}{" "}
                  {organization.accounts?.find(
                    (a) => a.id === confirmation.schedule?.source_account_id,
                  )?.public_number || confirmation.schedule?.source_account_id}
                </p>
                <p>
                  {confirmation.schedule?.frequency} ·{" "}
                  {confirmation.schedule?.local_time} ·{" "}
                  {confirmation.schedule?.timezone}
                </p>
                <p>
                  {ui("Лимит ведомости:")}{" "}
                  {money(confirmation.schedule?.max_amount || "0.00")}
                </p>
                {confirmation.schedule?.items.map((i) => (
                  <p key={i.employee_id}>
                    {i.user_name} · {i.account_id} · {money(i.amount)}
                  </p>
                ))}
                <p>
                  {ui(
                    "Вы утверждаете текущую ревизию расписания: автоматическое списание с указанного счёта выбранным получателям в пределах лимита.",
                  )}
                </p>
              </div>
            )}
          <button disabled={busy} onClick={() => void act()}>
            {busy ? ui("Проверяем…") : ui("Подтвердить")}
          </button>
          {confirmation.kind === "batch" &&
            ["execute", "retry"].includes(confirmation.action) && (
              <button
                className="secondary"
                disabled={busy}
                onClick={async () => {
                  try {
                    setDetail(
                      await businessApi.get<PayrollBatch>(
                        org,
                        "batches/" + confirmation.id,
                      ),
                    );
                    setConfirmation(undefined);
                    setError("");
                  } catch (e) {
                    setError(localError(e));
                  }
                }}
              >
                {ui("Проверить результат ведомости")}
              </button>
            )}
        </Modal>
      )}
    </>
  );
}
