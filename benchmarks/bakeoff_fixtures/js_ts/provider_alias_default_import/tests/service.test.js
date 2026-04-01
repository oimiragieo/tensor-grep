import { buildReceipt } from "../service.js";

test("buildReceipt", () => {
  expect(buildReceipt(2)).toBe(3);
});
