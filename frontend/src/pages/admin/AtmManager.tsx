import { localize as ui, useI18n as useLocale } from "../../i18n";
import { useEffect, useState } from "react";
import { request } from "../../api";
import { Loading, Modal } from "../../components/BankUI";
import { Notice } from "../../components/Notice";
import { dateTime, localError } from "../../i18n";
type Device = {
  id: string;
  username: string;
  is_master: boolean;
  enabled: boolean;
  connected: boolean;
  dimension: string;
  position: {
    x: number;
    y: number;
    z: number;
  };
  last_seen_at: string;
  inventory: number;
  free_space: number;
  error: string;
  container: Record<string, unknown>;
  current_operation: string | null;
};
type Operation = {
  id: string;
  device_id: string;
  nickname: string;
  kind: string;
  quantity: number;
  status: string;
  created_at: string;
  updated_at: string;
  error: string;
  evidence: Record<string, unknown>;
  operation_id: string | null;
  hold_id: string | null;
};
type Data = {
  devices: Device[];
  operations: Operation[];
  events: {
    id: number;
    action: string;
    device_id: string | null;
    operation_id: string | null;
    created_at: string;
    details: Record<string, unknown>;
  }[];
};
const statuses = (): Record<string, string> => ({
  CREATED: ui("Создана"),
  RESERVED: ui("Средства зарезервированы"),
  PROCESSING: ui("Физическая операция"),
  COMPLETED: ui("Завершена"),
  FAILED: ui("Отменена"),
  REQUIRES_REVIEW: ui("Требуется ручная сверка"),
});
const kinds = (): Record<string, string> => ({
  deposit: ui("Пополнение"),
  withdraw: ui("Снятие"),
  collection: ui("Инкассация"),
});
export function AtmManager({ permissions }: { permissions: string[] }) {
  useLocale();
  const [data, setData] = useState<Data>(),
    [error, setError] = useState("");
  const [device, setDevice] = useState(""),
    [status, setStatus] = useState(""),
    [offset, setOffset] = useState(0),
    [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<Operation>(),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  const manage = permissions.includes("admin.atm.manage");
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    request<Data>(
      "/admin/atm?" +
        new URLSearchParams({
          device_id: device,
          status,
          offset: String(offset),
        }),
      { signal: controller.signal },
    )
      .then(setData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(localError(e));
      });
    return () => controller.abort();
  }, [device, status, offset, reload]);
  async function change(run: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await run();
      setSelected(undefined);
      setReason("");
      setReload((x) => x + 1);
    } catch (e) {
      setError(localError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="atm-manager">
      <div className="section-heading">
        <h2>{ui("Банкоматы")}</h2>
        <button
          className="secondary"
          onClick={() => setReload((x) => x + 1)}
          disabled={busy}
        >
          {ui("Обновить")}
        </button>
      </div>
      <Notice error={error} />
      {!data && !error && <Loading />}
      {data && (
        <>
          <div className="account-grid">
            {data.devices.map((d) => {
              const online =
                d.connected && Date.now() - Date.parse(d.last_seen_at) < 15000;
              const active = data.operations.find(
                (o) =>
                  o.device_id === d.id &&
                  !["COMPLETED", "FAILED"].includes(o.status),
              );
              return (
                <section className="panel" key={d.id}>
                  <div className="section-heading">
                    <h3>{d.username}</h3>
                    <span className={"tag" + (online ? " positive" : "")}>
                      {online ? ui("Подключён") : ui("Не в сети")}
                    </span>
                  </div>
                  <p>
                    {d.is_master ? ui("Мастер и банкомат") : ui("Банкомат")} ·{" "}
                    {d.id}
                  </p>
                  <p>
                    {ui("Измерение: ")}
                    {d.dimension || "—"}
                    <br />
                    {ui("Координаты: ")}
                    {d.position.x?.toFixed(1)}, {d.position.y?.toFixed(1)},{" "}
                    {d.position.z?.toFixed(1)}
                  </p>
                  <p>
                    {ui("Дебрисов: ")}
                    <b>{d.inventory}</b>
                    {ui(" · Свободно: ")}
                    {d.free_space}
                  </p>
                  <small>
                    {ui("Активность: ")}
                    {dateTime(d.last_seen_at, undefined)}
                  </small>
                  <p>
                    {d.enabled
                      ? ui("Приём операций включён")
                      : ui("Приём операций отключён")}
                  </p>
                  {d.current_operation && (
                    <p>
                      {ui("Текущая операция: ")}
                      {d.current_operation}
                      {active ? " — " + statuses()[active.status] : ""}
                    </p>
                  )}
                  {d.error && <p className="field-error">{d.error}</p>}
                  <details>
                    <summary>{ui("Привязанный контейнер")}</summary>
                    <pre>{JSON.stringify(d.container, null, 2)}</pre>
                  </details>
                  {manage && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void change(() =>
                          request(
                            "/admin/atm/devices/" + encodeURIComponent(d.id),
                            {
                              method: "PATCH",
                              body: JSON.stringify({ enabled: !d.enabled }),
                            },
                          ),
                        )
                      }
                    >
                      {d.enabled
                        ? ui("Отключить новые операции")
                        : ui("Включить новые операции")}
                    </button>
                  )}
                </section>
              );
            })}
          </div>
          {!data.devices.length && (
            <p>{ui("Координатор ещё не зарегистрировал банкоматы.")}</p>
          )}
          <div className="payment-tabs">
            <label>
              {ui("Банкомат")}
              <select
                value={device}
                onChange={(e) => {
                  setDevice(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="">{ui("Все банкоматы")}</option>
                {data.devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.username}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {ui("Статус")}
              <select
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="">{ui("Все статусы")}</option>
                {Object.entries(statuses()).map(([s, name]) => (
                  <option key={s} value={s}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <h3>{ui("История пополнений, снятий и инкассаций")}</h3>
          {data.operations.map((o) => (
            <section className="panel" key={o.id}>
              <div className="section-heading">
                <b>
                  {kinds()[o.kind]} · {o.quantity}
                  {ui(" ДБ · ")}
                  {o.nickname}
                </b>
                <span className="tag">{statuses()[o.status]}</span>
              </div>
              <p>
                {o.device_id} · {dateTime(o.created_at, undefined)}
              </p>
              <small>{o.id}</small>
              {o.error && <p className="field-error">{o.error}</p>}
              <details>
                <summary>
                  {ui("Финансовая операция и физическое подтверждение")}
                </summary>
                <p>
                  {ui("Операция ядра: ")}
                  {o.operation_id || "—"}
                  <br />
                  {ui("Резерв: ")}
                  {o.hold_id || "—"}
                </p>
                <pre>{JSON.stringify(o.evidence, null, 2)}</pre>
              </details>
              {manage && o.status === "REQUIRES_REVIEW" && (
                <button className="secondary" onClick={() => setSelected(o)}>
                  {ui("Ручная сверка")}
                </button>
              )}
            </section>
          ))}
          <div className="payment-tabs">
            <button
              className="secondary"
              disabled={offset === 0}
              onClick={() => setOffset((x) => Math.max(0, x - 50))}
            >
              {ui("Назад")}
            </button>
            <span>
              {ui("Страница ")}
              {offset / 50 + 1}
            </span>
            <button
              className="secondary"
              disabled={data.operations.length < 50}
              onClick={() => setOffset((x) => x + 50)}
            >
              {ui("Далее")}
            </button>
          </div>
          <h3>{ui("Журнал событий и ошибок")}</h3>
          {data.events.map((e) => (
            <details className="panel" key={e.id}>
              <summary>
                {dateTime(e.created_at, undefined)} ·{" "}
                {e.device_id || ui("Координатор")} · {e.action}
              </summary>
              <p>{e.operation_id}</p>
              <pre>{JSON.stringify(e.details, null, 2)}</pre>
            </details>
          ))}
        </>
      )}
      {selected && (
        <Modal
          title={ui("Ручная сверка банкомата")}
          onClose={() => setSelected(undefined)}
        >
          <p>
            {ui(
              "Проверьте контейнер, инвентарь бота, журнал и фактическое получение предметов игроком. Решение изменит финансовое состояние без повторного перемещения предметов.",
            )}
          </p>
          <p>
            {kinds()[selected.kind]}: {selected.quantity}
            {ui(" ДБ · ")}
            {selected.nickname}
          </p>
          <Notice error={error} />
          <label>
            {ui("Основание и результат физической проверки")}
            <textarea
              required
              minLength={5}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <button
            disabled={busy || reason.trim().length < 5}
            onClick={() =>
              void change(() =>
                request("/admin/atm/operations/" + selected.id + "/resolve", {
                  method: "POST",
                  body: JSON.stringify({
                    decision: "settle",
                    reason,
                    confirmed: true,
                  }),
                }),
              )
            }
          >
            {ui("Предметы перемещены полностью — завершить расчёт")}
          </button>
          <button
            className="danger-button"
            disabled={busy || reason.trim().length < 5}
            onClick={() =>
              void change(() =>
                request("/admin/atm/operations/" + selected.id + "/resolve", {
                  method: "POST",
                  body: JSON.stringify({
                    decision: "cancel",
                    reason,
                    confirmed: true,
                  }),
                }),
              )
            }
          >
            {ui("Предметы возвращены — отменить и освободить резерв")}
          </button>
        </Modal>
      )}
    </div>
  );
}
