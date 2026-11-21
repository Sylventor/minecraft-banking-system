import { useEffect, useState } from "react";
import { api } from "../api";
import {
  dateTime,
  localError,
  money,
  operationName,
  operationStatus,
  useI18n,
} from "../i18n";
import type { Operation } from "../types";
import { Icon, Loading, Modal } from "./BankUI";
import { Notice } from "./Notice";

export function ReceiptDetails({
  operation,
  onRepeat,
}: {
  operation: Operation;
  onRepeat?: () => void;
}) {
  const { t, language } = useI18n();
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  function download() {
    const content = `${t("Квитанция Banking System", "Banking System receipt")}\n${operation.id}\n${dateTime(operation.created_at, language)}\n${operation.sender ?? "Banking System"} → ${operation.recipient ?? operation.destination_public_number ?? ""}\n${money(operation.amount.amount, language)}\n${t("Комиссия", "Fee")}: ${money(operation.fee?.amount ?? "0.00", language)}\n${operation.reason ?? ""}\n${operationStatus(operation.status, t)}`;
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/plain;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `horizon-${operation.id}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  async function save() {
    const profile = operation.recipient_profile;
    const recipient = profile?.id
      ? { kind: "user" as const, value: profile.id }
      : operation.destination_owner_id
        ? { kind: "user" as const, value: operation.destination_owner_id }
        : {
            kind: "account" as const,
            value:
              operation.destination_public_number ||
              operation.destination_account_id,
          };
    try {
      await api.saveRecipient(recipient);
      setSaved(true);
      setError("");
    } catch (e) {
      setError(localError(e, language));
    }
  }
  return (
    <div className="receipt">
      <div className="receipt-status">
        <Icon
          name={operation.status === "completed" ? "check" : "clock"}
          size={30}
        />
      </div>
      <p className="eyebrow">{operationName(operation.type, t)}</p>
      <h2>
        {operation.status === "completed"
          ? t("Операция выполнена", "Operation completed")
          : t("Детали операции", "Operation details")}
      </h2>
      <strong className="receipt-amount">
        {money(operation.amount.amount, language)}
      </strong>
      <Notice error={error} />
      <dl className="key-values">
        <div>
          <dt>{t("Отправитель", "Sender")}</dt>
          <dd>{operation.sender || operation.source_name || "Banking System"}</dd>
        </div>
        <div>
          <dt>{t("Получатель", "Recipient")}</dt>
          <dd>
            {operation.recipient ||
              operation.destination_name ||
              operation.destination_public_number}
          </dd>
        </div>
        <div>
          <dt>{t("Дата и время", "Date and time")}</dt>
          <dd>{dateTime(operation.created_at, language)}</dd>
        </div>
        <div>
          <dt>{t("Комиссия", "Fee")}</dt>
          <dd>{money(operation.fee?.amount ?? "0.00", language)}</dd>
        </div>
        {operation.reason && (
          <div>
            <dt>{t("Комментарий", "Comment")}</dt>
            <dd>{operation.reason}</dd>
          </div>
        )}
        <div>
          <dt>{t("Статус", "Status")}</dt>
          <dd className="positive">
            {operation.status === "completed"
              ? t("Выполнено", "Completed")
              : operationStatus(operation.status, t)}
          </dd>
        </div>
      </dl>
      <details className="technical-details">
        <summary>{t("Технические реквизиты", "Technical details")}</summary>
        <p>
          {t("Номер операции", "Operation ID")}
          <code>{operation.id}</code>
        </p>
        <p>
          {t("Со счёта", "From account")}
          <code>
            {operation.source_public_number || operation.source_account_id}
          </code>
        </p>
        <p>
          {t("На счёт", "To account")}
          <code>
            {operation.destination_public_number ||
              operation.destination_account_id}
          </code>
        </p>
      </details>
      <div className="button-row">
        <button className="secondary" onClick={download}>
          <Icon name="download" />
          {t("Скачать квитанцию", "Download receipt")}
        </button>
        {onRepeat && (
          <button onClick={onRepeat}>
            <Icon name="refresh" />
            {t("Повторить перевод", "Repeat transfer")}
          </button>
        )}
        {operation.type === "transfer" &&
          operation.direction !== "incoming" && (
            <button className="text-button" onClick={() => void save()}>
              <Icon name="star" />
              {saved
                ? t("Получатель сохранён", "Recipient saved")
                : t("Сохранить получателя", "Save recipient")}
            </button>
          )}
      </div>
    </div>
  );
}
export function ReceiptModal({
  id,
  onClose,
  onRepeat,
}: {
  id: string;
  onClose: () => void;
  onRepeat: (operation: Operation) => void;
}) {
  const { t, language } = useI18n();
  const [operation, setOperation] = useState<Operation>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .receipt(id)
      .then((value) => {
        if (active) setOperation(value);
      })
      .catch((e) => {
        if (active) setError(localError(e, language));
      });
    return () => {
      active = false;
    };
  }, [id, language]);
  return (
    <Modal title={t("Квитанция", "Receipt")} onClose={onClose}>
      <Notice error={error} />
      {operation ? (
        <ReceiptDetails
          operation={operation}
          onRepeat={
            operation.type === "transfer" || operation.type === "payment"
              ? () => onRepeat(operation)
              : undefined
          }
        />
      ) : (
        !error && <Loading />
      )}
    </Modal>
  );
}
