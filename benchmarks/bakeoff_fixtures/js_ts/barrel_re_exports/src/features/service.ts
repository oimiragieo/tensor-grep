import { createInvoiceBarrel } from "../index";

export function buildReceiptBarrel(total: number) {
  return createInvoiceBarrel(total);
}
