import { request } from "./api";

export type AdminPage<T> = {
  items: T[];
  next_cursor?: string | null;
  next_offset?: number | null;
};
export type AdminUser = {
  id: string;
  bank_username: string;
  display_name: string;
  status: string;
  discord_id?: string;
  minecraft_nickname?: string;
};
export type Role = {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  system_key?: string;
  members: number;
};
export type Effective = {
  permissions: string[];
  roles: Role[];
  legacy_permissions: { permission: string }[];
};
export type AdminAccount = {
  id: string;
  name: string;
  public_number: string;
  owner_id?: string;
  owner: string;
  status: string;
  balance: string;
  held: string;
};
export type AdminOperation = {
  id: string;
  type: string;
  status: string;
  amount: string;
  source_account_id: string;
  destination_account_id: string;
  source_name: string;
  destination_name: string;
  source_public_number: string;
  destination_public_number: string;
  reason: string;
  created_at: string;
};
export type TermsVersion = {
  id: string;
  version: number;
  markdown: string;
  status: string;
  author?: string;
  published_at: string;
};
export type Delivery = {
  id: string;
  title: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
};
export type NotificationPreferences = {
  discord_enabled: boolean;
  security_enabled: boolean;
  transfers_enabled: boolean;
  incoming_enabled: boolean;
  outgoing_enabled: boolean;
  organizations_enabled: boolean;
};
export function query(
  params: Record<string, string | number | undefined | null>,
) {
  return new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}
export const adminApi = {
  get: <T>(path: string) => request<T>("/admin/" + path),
  write: <T>(path: string, body: unknown, method = "POST") =>
    request<T>("/admin/" + path, { method, body: JSON.stringify(body) }),
  users: (q = "", cursor = "") =>
    request<AdminPage<AdminUser>>(
      "/admin/users?" + query({ query: q, cursor, limit: 20 }),
    ),
  accounts: (q = "", cursor = "", owner?: string) =>
    request<AdminPage<AdminAccount>>(
      "/admin/accounts?" + query({ q, cursor, owner_id: owner, limit: 20 }),
    ),
  roles: () => request<AdminPage<Role>>("/admin/roles?limit=100"),
  effective: (id: string) =>
    request<Effective>(`/admin/users/${encodeURIComponent(id)}/permissions`),
};
export const termsApi = {
  current: () => request<{ current: TermsVersion | null }>("/terms"),
};
export const notificationApi = {
  preferences: () =>
    request<NotificationPreferences>("/me/notification-settings"),
  save: (body: NotificationPreferences) =>
    request<NotificationPreferences>("/me/notification-settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deliveries: (offset = 0) =>
    request<AdminPage<Delivery> & { worker_online: boolean }>(
      "/me/notification-deliveries?" + query({ offset }),
    ),
};
