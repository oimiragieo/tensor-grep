import { createInvoiceMultiExport, formatInvoiceLabel } from "./payments";

export function buildReceiptMultiExport(total) {
  return `${formatInvoiceLabel(total)}:${createInvoiceMultiExport(total)}`;
}
