export type Money = { amount: string; currency: "DB" };
export type AccountBalance = {
  id: string;
  status: string;
  account_type: string;
  balance: Money;
  held: Money;
  available: Money;
  public_number: string;
  name: string;
  is_primary: boolean;
  owner_id: string;
  organization_id: string;
  permissions: string[];
};
export type Operation = {
  id: string;
  type: string;
  status: string;
  source_account_id: string;
  destination_account_id: string;
  amount: Money;
  created_at: string;
  completed_at: string;
  reason?: string;
  fee?: Money;
  sender?: string;
  recipient?: string;
  counterparty?: string;
  direction?: "incoming" | "outgoing" | "internal";
  source_public_number?: string;
  destination_public_number?: string;
  source_owner_id?: string;
  destination_owner_id?: string;
  source_name?: string;
  destination_name?: string;
  recipient_profile?: Recipient;
};
export type ApiKey = {
  organization_id?: string | null;
  id: string;
  prefix: string;
  name: string;
  scopes: string[];
  created_at: string;
  revoked_at: string | null;
};
export type ApiKeyCreated = ApiKey & { secret: string };
export type ApiError = { error: string; code: string; request_id: string };
export type Page<T> = { items: T[]; next_cursor: string | null };
export type Profile = {
  id: string;
  scopes: string[];
  permissions: string[];
  via_api_key: boolean;
  display_name: string;
  bank_username: string | null;
  language: "ru" | "en" | "uk-UA";
  banking_ready: boolean;
  discord: { username: string; id: string } | null;
  minecraft: { nickname: string; player_uuid: string } | null;
  notifications: {
    discord_enabled: boolean;
    security_enabled: boolean;
    transfers_enabled: boolean;
  };
  discord_notifications_available: boolean;
};
export type Organization = {
  id: string;
  name: string;
  owner_id?: string;
  current_role?: "owner" | "administrator" | "member";
  members?: OrganizationMember[];
  accounts?: AccountBalance[];
};
export type OrganizationMember = {
  user_id: string;
  role: "owner" | "administrator" | "member";
  display_name: string;
  bank_username: string | null;
  permissions: string[];
};
export type Investigation = {
  id: string;
  status: string;
  subject_type: string;
  subject_id: string;
  created_at: string;
};
export type AuditEvent = {
  id: string;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  created_at: string;
};
export type MinecraftIdentity = {
  player_uuid: string;
  nickname: string;
  verified_at: string;
};
export type MinecraftProfile = {
  integration_enabled: boolean;
  link: MinecraftIdentity | null;
};
export type MinecraftChallenge = {
  challenge_id: string;
  code: string;
  expires_at: string;
  note: string;
};
export type RecipientReference = {
  kind: "user" | "bank_name" | "minecraft" | "discord" | "account";
  value: string;
};
export type Recipient = {
  id: string | null;
  kind: "user" | "account";
  display_name: string;
  bank_username: string | null;
  minecraft_nickname?: string | null;
  discord_username?: string | null;
  account_id: string;
  account_name: string;
  public_number: string;
  organization_id: string;
  owner_id: string;
  saved_id?: string;
  label?: string;
  available?: boolean;
};
export type TransferQuote = {
  internal?: boolean;
  id: string;
  source: AccountBalance;
  recipient: Recipient;
  amount: Money;
  fee: Money;
  total: Money;
  reason: string;
  expires_at: string;
  idempotency_key: string;
  operation_id?: string | null;
};
export type Dashboard = {
  profile: Profile;
  primary_account: AccountBalance;
  accounts: AccountBalance[];
  total_available: Money;
  recent_operations: Operation[];
  unread_notifications: number;
};
export type Notification = {
  id: string;
  kind: "transfer" | "security" | "pending" | "failed" | "organization";
  title: string;
  body: string;
  operation_id: string | null;
  created_at: string;
  read_at: string | null;
};
export type Session = {
  id: string;
  current: boolean;
  created_at: string;
  expires_at: string;
  last_activity_at?: string;
  absolute_expires_at?: string;
  client_info?: string;
};
export type ActivityFilters = {
  account_id?: string;
  operation_type?: string;
  q?: string;
  from_date?: string;
  to_date?: string;
  order?: "asc" | "desc";
  cursor?: string;
  limit?: string;
};
