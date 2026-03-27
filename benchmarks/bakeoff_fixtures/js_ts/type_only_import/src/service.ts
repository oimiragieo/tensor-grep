import type { InvoicePayload } from "./types";

export function normalizeInvoice(payload: InvoicePayload): number {
  return payload.total;
}
