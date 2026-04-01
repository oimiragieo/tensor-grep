import { createInvoiceChain } from "./barrel_two";

export function buildReceiptChain(total: number) {
  return createInvoiceChain(total);
}
