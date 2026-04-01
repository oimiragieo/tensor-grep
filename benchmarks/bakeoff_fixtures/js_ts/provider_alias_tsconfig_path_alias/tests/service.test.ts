import { buildReceiptProviderAlias } from "../src/service";

test("buildReceiptProviderAlias uses aliased provider path", () => {
  expect(buildReceiptProviderAlias(2)).toBe(3);
});
