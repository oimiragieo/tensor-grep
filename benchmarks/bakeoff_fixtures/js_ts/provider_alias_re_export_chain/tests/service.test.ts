import { buildReceiptProviderChain } from "../src/service";

test("buildReceiptProviderChain uses re-export alias", () => {
  expect(buildReceiptProviderChain(2)).toBe(4);
});
