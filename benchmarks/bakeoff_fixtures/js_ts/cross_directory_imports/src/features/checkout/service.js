import { createInvoiceNested } from "../../core/payments";

export function buildReceiptNested(total) {
  return createInvoiceNested(total);
}
