import createInvoiceDefaultWrapper from "./payments.js";

const runInvoice = createInvoiceDefaultWrapper;

export function buildReceipt(total) {
  return runInvoice(total);
}
