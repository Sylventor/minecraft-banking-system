import { localize as ui } from "./i18n";
import { request } from "./api";
export const exchangeStatus = (value: string) =>
  (
    ({
      draft: ui("Черновик"),
      pending_review: ui("На рассмотрении"),
      approved: ui("Утверждено"),
      rejected: ui("Отклонено"),
      active: ui("Активно"),
      suspended: ui("Торги приостановлены"),
      delisted: ui("Снято с торгов"),
      open: ui("Открыта"),
      partially_filled: ui("Частично исполнена"),
      filled: ui("Исполнена"),
      cancelled: ui("Остаток отменён"),
      expired: ui("Истекла"),
      completed: ui("Выплачено"),
      pending: ui("Ожидает выплаты"),
      paid: ui("Выплачено"),
      failed: ui("Ошибка"),
    }) as Record<string, string>
  )[value] || value;
export type Page<T> = {
  items: T[];
  next_cursor: string | null;
};
export type Stock = {
  created_at?: string;
  reason?: string;
  id: string;
  ticker: string;
  name: string;
  description: string;
  terms: string;
  rights: string;
  status: string;
  total_quantity: number;
  last_price: string | null;
  last_trade_at: string | null;
  change: string | null;
  change_percent: string | null;
  volume24: number;
  trades24: number;
  best_bid: string | null;
  best_ask: string | null;
  ipo: {
    price: string;
    quantity: number;
    remaining: number;
    starts_at: string;
    ends_at: string;
    max_per_buyer: number;
    status: string;
  } | null;
};
export type Order = {
  id: string;
  stock_id: string;
  side: string;
  kind: string;
  quantity: number;
  remaining: number;
  price: string;
  status: string;
  created_at: string;
  expires_at: string;
  account_id: string;
  reserved_cash?: string;
};
export type Trade = {
  id: string;
  quantity: number;
  price: string;
  total: string;
  fee: string;
  created_at: string;
  side?: string;
  ticker?: string;
  operation_id?: string;
};
export type Position = {
  stock_id: string;
  ticker: string;
  name: string;
  quantity: number;
  available: number;
  reserved: number;
  average_price: string;
  last_price: string | null;
  last_trade_at: string | null;
  estimated_value: string | null;
  unrealized: string | null;
  realized: string;
  dividends: string;
};
export type Candle = {
  at: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
  trades: number;
};
export type Distribution = {
  id: string;
  amount: string;
  account_id: string;
  quantity: number;
  status: string;
  operation_id: string | null;
  ticker?: string;
  pay_at?: string;
};
export type Dividend = {
  id: string;
  stock_id: string;
  amount: string;
  mode: string;
  total: string;
  rounding_remainder: string;
  record_at: string;
  pay_at: string;
  status: string;
  created_by: string;
  approved_by: string | null;
  items: Distribution[];
};
function query(params: Record<string, string>): string {
  const value = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== ""),
  ).toString();
  return value ? "?" + value : "";
}
export const exchangeApi = {
  get: <T>(path: string, params: Record<string, string> = {}) =>
    request<T>("/exchange/" + path + query(params)),
  write: <T>(path: string, body: unknown, key: string) =>
    request<T>("/exchange/" + path, {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Idempotency-Key": key },
    }),
  admin: <T>(path: string, params: Record<string, string> = {}) =>
    request<T>("/admin/exchange/" + path + query(params)),
  moderate: <T>(id: string, body: unknown, key: string, additional = false) =>
    request<T>(
      "/admin/exchange/issuers/" +
        id +
        (additional ? "/additional" : "/moderate"),
      {
        method: "POST",
        body: JSON.stringify(body),
        headers: { "Idempotency-Key": key },
      },
    ),
};
