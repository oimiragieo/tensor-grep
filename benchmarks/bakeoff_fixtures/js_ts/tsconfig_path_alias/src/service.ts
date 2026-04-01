import { createInvoicePathAlias } from "@app/payments";

export function buildReceiptPathAlias(total: number) {
  return createInvoicePathAlias(total);
}
