import { localize as ui } from "./i18n";
import { request } from "./api";
export type BusinessPage<T> = {
  items: T[];
  next_cursor: string | null;
};
export type Employee = {
  id: string;
  user_id: string;
  user_name: string;
  account_id: string;
  salary: string;
  frequency: string;
  position: string;
  note: string;
  active: boolean;
  revision: number;
};
export type PayrollItem = {
  id: string;
  employee_id: string;
  user_id: string;
  user_name: string;
  destination_account_id: string;
  amount: string;
  status: string;
  operation_id: string | null;
  error_code: string | null;
  paid_at: string | null;
};
export type PayrollBatch = {
  id: string;
  source_account_id: string;
  description: string;
  total: string;
  fee: string;
  status: string;
  created_by: string;
  approved_by: string | null;
  executor_id: string | null;
  created_at: string;
  completed_at: string | null;
  items: PayrollItem[];
};
export type PayrollSchedule = {
  id: string;
  source_account_id: string;
  name: string;
  frequency: "weekly" | "biweekly" | "monthly";
  weekday: number;
  month_day: number;
  local_time: string;
  timezone: string;
  mode: "manual" | "automatic";
  max_amount: string;
  status: string;
  revision: number;
  approved_by: string | null;
  next_run_at: string;
  last_error: string | null;
  items: {
    employee_id: string;
    amount: string;
    user_id: string;
    user_name: string;
    account_id: string;
  }[];
};
export type Metrics = {
  incoming: string;
  outgoing: string;
  net_cashflow: string;
  incoming_count: number;
  outgoing_count: number;
  average_incoming: string;
  average_outgoing: string;
  largest_incoming: string;
  largest_outgoing: string;
};
export type AnalyticsSummary = {
  balance: string;
  held: string;
  account_count: number;
  current: Metrics;
  previous: Metrics;
  changes: Record<
    string,
    {
      delta: string;
      percent: string | null;
    }
  >;
  period: Record<string, string>;
};
export type AnalyticsPoint = {
  at: string;
  incoming: string;
  outgoing: string;
  balance: string;
  activity: number;
};
export type Category = {
  key: string;
  name: string;
  incoming: string;
  outgoing: string;
  count: number;
};
export type Counterparty = {
  public_number: string;
  name: string;
  direction: string;
  volume: string;
  count: number;
  last_operation: string;
};
export type BusinessOperation = {
  id: string;
  created_at: string;
  type: string;
  status: string;
  reason: string;
  category: string;
  amount: string;
  direction: string;
};
const base = (org: string) => `/organizations/${encodeURIComponent(org)}`;
export const businessApi = {
  permissions: (org: string) =>
    request<{
      permissions: string[];
    }>(base(org) + "/business-permissions"),
  analytics: <T>(org: string, kind: string, params: URLSearchParams) =>
    request<T>(base(org) + `/analytics/${kind}?${params}`),
  get: <T>(org: string, path: string) =>
    request<T>(base(org) + "/payroll/" + path),
  write: <T>(
    org: string,
    path: string,
    body: unknown,
    method = "POST",
    key?: string,
  ) =>
    request<T>(base(org) + "/payroll/" + path, {
      method,
      body: JSON.stringify(body),
      headers: key ? { "Idempotency-Key": key } : undefined,
    }),
  category: (org: string, op: string, category: string) =>
    request(base(org) + `/analytics/transactions/${op}/category`, {
      method: "PUT",
      body: JSON.stringify({ category }),
    }),
  addCategory: (org: string, name: string) =>
    request(base(org) + "/analytics/categories", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  export: async (org: string, params: URLSearchParams) => {
    const response = await fetch(
      (import.meta.env.VITE_API_BASE ?? "/api/v1") +
        base(org) +
        "/analytics/export?" +
        params,
      { credentials: "include" },
    );
    if (!response.ok)
      throw new Error(ui("Экспорт отклонён. Проверьте права и фильтры."));
    const href = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = href;
    a.download = "horizon-business.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    return response.headers.get("X-Next-Cursor");
  },
};
