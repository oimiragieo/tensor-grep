import { createInvoiceProviderAlias as invoice } from "@app/payments";

const runInvoice = invoice;

export function buildReceiptProviderAlias(total: number) {
  return runInvoice(total);
}
