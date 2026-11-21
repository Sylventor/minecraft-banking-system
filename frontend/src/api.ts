import { localize as ui } from "./i18n";
import type {
  AccountBalance,
  ApiError,
  ApiKey,
  ApiKeyCreated,
  Operation,
  Page,
  Profile,
  Organization,
  Investigation,
  AuditEvent,
  MinecraftProfile,
  MinecraftChallenge,
  Dashboard,
  Recipient,
  RecipientReference,
  TransferQuote,
  Notification,
  Session,
  ActivityFilters,
} from "./types";
const base = import.meta.env.VITE_API_BASE ?? "/api/v1";
let requestLanguage =
  typeof window !== "undefined"
    ? (window.localStorage.getItem("horizon.language") ?? "ru")
    : "ru";
export function setRequestLanguage(value: string) {
  requestLanguage = value;
}
export class ApiFailure extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly status: number;
  constructor(data: ApiError, status = 400) {
    super(data.error);
    this.code = data.code;
    this.requestId = data.request_id;
    this.status = status;
  }
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept-Language", requestLanguage);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(base + path, {
    ...init,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    let data: ApiError;
    try {
      data = (await response.json()) as ApiError;
    } catch {
      data = {
        error: `HTTP ${response.status}`,
        code: "http_error",
        request_id: response.headers.get("X-Request-ID") ?? "",
      };
    }
    throw new ApiFailure(data, response.status);
  }
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}
export type TransferInput = {
  source_account_id: string;
  destination_account_id: string;
  amount: string;
  reason: string;
};
export const api = {
  me: () => request<Profile>("/me"),
  dashboard: () => request<Dashboard>("/dashboard"),
  updateSettings: (
    payload: Partial<
      Pick<Profile, "bank_username" | "display_name" | "language">
    > &
      Partial<Profile["notifications"]>,
  ) =>
    request<Profile>("/me/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  updateAccount: (
    id: string,
    payload: {
      name?: string;
      is_primary?: boolean;
    },
  ) =>
    request<AccountBalance>(`/accounts/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  searchRecipients: (q: string, signal?: AbortSignal) =>
    request<{
      items: Recipient[];
    }>(`/recipients/search?q=${encodeURIComponent(q)}`, { signal }),
  resolveRecipient: (recipient: RecipientReference) =>
    request<Recipient>("/recipients/resolve", {
      method: "POST",
      body: JSON.stringify(recipient),
    }),
  savedRecipients: () => request<Recipient[]>("/recipients/saved"),
  recentRecipients: () => request<Recipient[]>("/recipients/recent"),
  saveRecipient: (recipient: RecipientReference, label = "") =>
    request<{
      id: string;
    }>("/recipients/saved", {
      method: "POST",
      body: JSON.stringify({ recipient, label }),
    }),
  unsaveRecipient: (id: string) =>
    request<void>(`/recipients/saved/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  prepareTransfer: (payload: {
    internal?: boolean;
    recipient: RecipientReference;
    source_account_id?: string;
    amount: string;
    reason: string;
  }) =>
    request<TransferQuote>("/transfer-quotes", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  quote: (id: string) =>
    request<TransferQuote>(`/transfer-quotes/${encodeURIComponent(id)}`),
  confirmTransfer: (quote: TransferQuote) =>
    request<Operation>(
      `/transfer-quotes/${encodeURIComponent(quote.id)}/confirm`,
      { method: "POST", headers: { "Idempotency-Key": quote.idempotency_key } },
    ),
  activity: (filters: ActivityFilters = {}) =>
    request<Page<Operation>>(
      `/activity?${new URLSearchParams(
        Object.entries(filters).filter(
          ([, value]) => value !== undefined && value !== "",
        ) as [string, string][],
      ).toString()}`,
    ),
  receipt: (id: string) =>
    request<Operation>(`/receipts/${encodeURIComponent(id)}`),
  notifications: (cursor = "") =>
    request<
      Page<Notification> & {
        unread: number;
        discord_delivery_available: boolean;
      }
    >(`/notifications${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),
  readNotifications: (ids: string[] = [], all = false) =>
    request<void>("/notifications/read", {
      method: "POST",
      body: JSON.stringify({ ids, all }),
    }),
  sessions: () => request<Session[]>("/me/sessions"),
  revokeOtherSessions: () =>
    request<void>("/me/sessions/revoke-others", { method: "POST" }),
  rotateSession: () =>
    request<void>("/auth/session/refresh", { method: "POST" }),
  revokeSession: (id: string) =>
    request<void>(`/me/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  organization: (id: string) =>
    request<Organization>(`/organizations/${encodeURIComponent(id)}`),
  organizationAccount: (id: string, name: string, key: string) =>
    request<AccountBalance>(
      `/organizations/${encodeURIComponent(id)}/accounts`,
      {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({ name }),
      },
    ),
  organizationMember: (
    id: string,
    user: string,
    role: "member" | "administrator",
    permissions: string[],
  ) =>
    request<Organization>(
      `/organizations/${encodeURIComponent(id)}/members/${encodeURIComponent(user)}`,
      {
        method: "PUT",
        body: JSON.stringify({ user_id: user, role, permissions }),
      },
    ),
  removeOrganizationMember: (id: string, user: string) =>
    request<void>(
      `/organizations/${encodeURIComponent(id)}/members/${encodeURIComponent(user)}`,
      { method: "DELETE" },
    ),
  minecraft: () => request<MinecraftProfile>("/minecraft"),
  minecraftCode: () =>
    request<MinecraftChallenge>("/minecraft/link-code", { method: "POST" }),
  unlinkMinecraft: () => request<void>("/minecraft", { method: "DELETE" }),
  accounts: (cursor = "") =>
    request<Page<AccountBalance>>(
      `/accounts?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    ),
  createAccount: (name = ui("Личный счёт"), key = crypto.randomUUID()) =>
    request<AccountBalance>("/accounts", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ account_type: "personal", name }),
    }),
  balance: (id: string) =>
    request<AccountBalance>(`/accounts/${encodeURIComponent(id)}/balance`),
  history: (id: string, cursor = "") =>
    request<Page<Operation>>(
      `/accounts/${encodeURIComponent(id)}/operations?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    ),
  transfer: (payload: TransferInput, key: string) =>
    request<Operation>("/transfers", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(payload),
    }),
  keys: () => request<ApiKey[]>("/api-keys"),
  createKey: (
    name: string,
    scopes: string[],
    account_ids: string[],
    organization_id?: string,
  ) =>
    request<ApiKeyCreated>("/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, scopes, account_ids, organization_id }),
    }),
  revokeKey: (id: string) =>
    request<void>(`/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  organizations: () => request<Organization[]>("/organizations"),
  createOrganization: (name: string) =>
    request<Organization>("/organizations", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  audit: () => request<Page<AuditEvent>>("/admin/audit"),
  investigations: () => request<Investigation[]>("/admin/investigations"),
  createInvestigation: (
    subject_type: string,
    subject_id: string,
    note: string,
  ) =>
    request<Investigation>("/admin/investigations", {
      method: "POST",
      body: JSON.stringify({ subject_type, subject_id, note }),
    }),
  issue: (
    target_account_id: string,
    amount: string,
    reason: string,
    authority_reference: string,
    key: string,
  ) =>
    request<Operation>("/admin/issuances", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({
        target_account_id,
        amount,
        reason,
        authority_reference,
      }),
    }),
  refund: (
    source_account_id: string,
    original_operation_id: string,
    amount: string,
    reason: string,
    key: string,
  ) =>
    request<Operation>("/admin/refunds", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({
        source_account_id,
        original_operation_id,
        amount,
        reason,
      }),
    }),
  logout: async () => {
    await request<void>("/auth/logout", { method: "POST" });
    // Keep display preferences, remove account drafts and pending confirmations.
    for (const storage of [localStorage, sessionStorage]) {
      for (const key of Object.keys(storage)) {
        if (
          key.startsWith("horizon.") &&
          ![
            "horizon.language",
            "horizon.theme",
            "horizon.hideAmounts",
          ].includes(key)
        )
          storage.removeItem(key);
      }
    }
  },
  discordLogin: (ageConfirmed: unknown = false) => {
    if (/^\/payments(?:\/[0-9a-f-]{36})?\/?$/.test(location.pathname))
      sessionStorage.setItem("horizon.payment-return", location.pathname);
    window.location.assign(
      base +
        "/auth/discord/login" +
        (ageConfirmed === true ? "?age_confirmed=true" : ""),
    );
  },
};
