import { invoiceProviderChain } from "./barrel_two";

const runInvoiceChain = invoiceProviderChain;

export function buildReceiptProviderChain(total: number) {
  return runInvoiceChain(total);
}
