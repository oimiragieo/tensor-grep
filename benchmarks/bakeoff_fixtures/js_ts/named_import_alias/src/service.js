import { createInvoiceAlias as makeInvoice } from "./payments";

export function buildReceiptAlias(total) {
  return makeInvoice(total);
}
