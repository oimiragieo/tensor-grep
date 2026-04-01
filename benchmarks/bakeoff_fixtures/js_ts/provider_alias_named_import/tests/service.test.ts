import { buildReceipt } from "../service";

test("buildReceipt", () => {
  expect(buildReceipt(2)).toBe(3);
});
