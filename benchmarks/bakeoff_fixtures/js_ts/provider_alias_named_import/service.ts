import { createInvoiceAliasWrapper as invoice } from "./payments";

const runInvoice = invoice;

export function buildReceipt(total: number) {
  return runInvoice(total);
}
