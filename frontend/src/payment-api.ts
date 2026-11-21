import { request } from "./api";

export type PaymentStatus =
  "pending" | "paid" | "declined" | "cancelled" | "expired";
export type PaymentRequest = {
  id: string;
  creator_id: string;
  payer_id: string;
  organization_id: string | null;
  creator_name: string;
  payer_name: string;
  merchant_name: string;
  recipient_account_id: string;
  recipient_account_name: string;
  recipient_public_number: string;
  amount: string;
  currency: "DB";
  description: string;
  status: PaymentStatus;
  external_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  transaction_id: string | null;
};
export type PaymentPage = {
  items: PaymentRequest[];
  next_offset: number | null;
  total?: number;
};
export const paymentApi = {
  list: (direction: string, params: URLSearchParams) =>
    request<PaymentPage>(`/payment-requests/${direction}?${params}`),
  get: (id: string) =>
    request<PaymentRequest>(`/payment-requests/${encodeURIComponent(id)}`),
  create: (
    body: {
      payer_id: string;
      recipient_account_id: string;
      amount: string;
      description: string;
      expires_in: number;
      external_id?: string;
    },
    key: string,
  ) =>
    request<PaymentRequest>("/payment-requests", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    }),
  pay: (id: string, source: string, key: string) =>
    request<PaymentRequest>(`/payment-requests/${id}/pay`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ source_account_id: source, confirmed: true }),
    }),
  action: (id: string, action: "decline" | "cancel") =>
    request<PaymentRequest>(`/payment-requests/${id}/${action}`, {
      method: "POST",
    }),
};
