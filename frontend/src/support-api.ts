import { localize as ui } from "./i18n";
import { request } from "./api";
export const supportCategories = (): Record<string, string> => ({
  account: ui("Проблемы с аккаунтом"),
  payments: ui("Переводы и платежи"),
  atm: ui("Банкоматы Minecraft"),
  organizations: ui("Организации и счета"),
  exchange: ui("Фондовая биржа"),
  website: ui("Ошибки сайта"),
  feedback: ui("Жалобы и предложения"),
  other: ui("Другое"),
});
export const supportStatuses = (): Record<string, string> => ({
  open: ui("Открыто"),
  working: ui("В работе"),
  waiting: ui("Ожидает ответа пользователя"),
  resolved: ui("Решено"),
  closed: ui("Закрыто"),
});
export type SupportMessage = {
  id: number;
  author_id: string;
  author: string;
  sender_kind: string;
  body: string;
  created_at: string;
};
export type SupportTicket = {
  id: string;
  number: number;
  title: string;
  category: string;
  status: string;
  assigned_to: string | null;
  user_name?: string;
  updated_at: string;
  unread: number;
  last_message?: string;
  messages?: SupportMessage[];
  notes?: SupportMessage[];
  has_older?: boolean;
  notes_has_older?: boolean;
};
export const supportPath = (admin = false) =>
  admin ? "/admin/support/tickets" : "/support/tickets";
export const supportWrite = <T>(path: string, body: unknown, method = "POST") =>
  request<T>(path, { method, body: JSON.stringify(body) });
