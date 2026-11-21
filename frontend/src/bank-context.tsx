import { createContext, useContext } from "react";
import type { Dashboard, Operation, RecipientReference } from "./types";

export type Section =
  | "home"
  | "transfers"
  | "payments"
  | "exchange"
  | "investments"
  | "accounts"
  | "history"
  | "organizations"
  | "notifications"
  | "settings"
  | "developers"
  | "more"
  | "admin";
export type TransferPreset = {
  recipient?: RecipientReference;
  amount?: string;
  reason?: string;
  source?: string;
  mode?: "external" | "internal";
  nonce: number;
};
export type BankContext = {
  data: Dashboard;
  refresh: () => Promise<void>;
  navigate: (section: Section, accountId?: string) => void;
  historyAccount?: string;
  openReceipt: (id: string) => void;
  transferTo: (
    recipient: RecipientReference,
    amount?: string,
    reason?: string,
    source?: string,
  ) => void;
  repeatTransfer: (operation: Operation) => void;
  transferPreset?: TransferPreset;
  betweenAccounts: (source?: string) => void;
};
export const bankContext = createContext<BankContext | null>(null);
export function useBank(): BankContext {
  const value = useContext(bankContext);
  if (!value) throw new Error("Bank provider missing");
  return value;
}
