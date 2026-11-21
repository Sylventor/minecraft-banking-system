// Test-only providers. This module is never imported by the application entry point.
import type { ReactNode } from "react";
import { bankContext, type BankContext } from "./bank-context";
import { I18nProvider } from "./i18n";
import type {
  AccountBalance,
  Dashboard,
  Profile,
  Recipient,
  TransferQuote,
} from "./types";

export const testProfile: Profile = {
  id: "00000000-0000-0000-0000-000000000010",
  scopes: [],
  permissions: [],
  via_api_key: false,
  display_name: "DemoOwner",
  bank_username: "DemoOwner",
  language: "ru",
  banking_ready: true,
  discord: { username: "demoowner", id: "123456789012345678" },
  minecraft: null,
  notifications: {
    discord_enabled: true,
    security_enabled: true,
    transfers_enabled: true,
  },
  discord_notifications_available: false,
};
export const testAccount: AccountBalance = {
  id: "00000000-0000-0000-0000-000000000011",
  status: "active",
  account_type: "personal",
  name: "Основной счёт",
  public_number: "HB-0123456789ABCDEF",
  is_primary: true,
  owner_id: testProfile.id,
  organization_id: "",
  permissions: [
    "accounts.read",
    "balance.read",
    "history.read",
    "transfer",
    "exchange.trade",
    "account.manage",
  ],
  balance: { amount: "100.00", currency: "DB" },
  held: { amount: "0.00", currency: "DB" },
  available: { amount: "100.00", currency: "DB" },
};
export const testRecipient: Recipient = {
  id: "00000000-0000-0000-0000-000000000020",
  kind: "user",
  display_name: "DemoPlayer",
  bank_username: "DemoPlayer",
  minecraft_nickname: "NemMinecraft",
  account_id: "00000000-0000-0000-0000-000000000021",
  account_name: "Основной счёт",
  public_number: "HB-FEDCBA9876543210",
  organization_id: "",
  owner_id: "00000000-0000-0000-0000-000000000020",
};
export const testQuote: TransferQuote = {
  id: "00000000-0000-0000-0000-000000000030",
  idempotency_key: "00000000-0000-0000-0000-000000000030",
  source: testAccount,
  recipient: testRecipient,
  amount: { amount: "25.50", currency: "DB" },
  fee: { amount: "0.00", currency: "DB" },
  total: { amount: "25.50", currency: "DB" },
  reason: "Спасибо",
  expires_at: "2030-01-01T00:10:00Z",
};
export const testDashboard: Dashboard = {
  profile: testProfile,
  primary_account: testAccount,
  accounts: [testAccount],
  total_available: testAccount.available,
  recent_operations: [],
  unread_notifications: 0,
};
export function TestBank({
  children,
  overrides = {},
}: {
  children: ReactNode;
  overrides?: Partial<BankContext>;
}) {
  const value: BankContext = {
    data: testDashboard,
    refresh: async () => undefined,
    navigate: () => undefined,
    openReceipt: () => undefined,
    transferTo: () => undefined,
    betweenAccounts: () => undefined,
    repeatTransfer: () => undefined,
    ...overrides,
  };
  return (
    <I18nProvider>
      <bankContext.Provider value={value}>{children}</bankContext.Provider>
    </I18nProvider>
  );
}
