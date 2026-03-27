import makeInvoice from "./payments";

export function buildReceiptDefault(total) {
  return makeInvoice(total);
}
