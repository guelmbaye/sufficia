import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const SHOTS = process.env.SCREENSHOT_DIR ?? resolve(__dirname, "../../../docs/evidence/screenshots");

/* Document 05 §42 and §128: the 20-second Grand Prize story, end to end, in a real browser. */
test("grand prize: denied, then authorized, executed, receipt, replay", async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true });
  const t0 = Date.now();
  await page.goto("/demo");

  // Denied: intention, failing evidence, decision and protected amount are all visible.
  const gate = page.getByTestId("gate");
  await expect(gate).toHaveAttribute("data-state", "DENY");
  await expect(page.getByTestId("decision-headline")).toHaveText("Execution denied");
  await expect(gate).toContainText("Available liquidity is $92,440. The Evidence Contract requires more than $500,000.");
  await expect(page.getByTestId("consequence")).toContainText("100 USDC did not move");
  await expect(page.getByTestId("row-req_liquidity")).toHaveAttribute("data-status", "FAIL");
  await expect(page.getByTestId("protected-capital")).toContainText("100 USDC");
  await expect(page.getByTestId("protected-capital")).not.toContainText("Executed");
  await expect(page.getByTestId("execute-disabled")).toBeDisabled();
  await expect(page.getByTestId("connectors").locator('path[data-causal="true"]')).toHaveCount(1);
  expect(Date.now() - t0).toBeLessThan(20_000);
  await page.waitForTimeout(700); // let the connector finish drawing
  await page.screenshot({ path: `${SHOTS}/A-denied.png` });

  // Same agent, same intent: only the evidence changes.
  await page.getByTestId("condition-valid").click();
  await expect(gate).toHaveAttribute("data-state", "AUTHORIZE");
  await expect(page.getByTestId("decision-headline")).toHaveText("Execution authorized");
  await expect(page.getByTestId("row-req_liquidity")).toContainText("$718,000");
  await expect(page.getByTestId("authorization")).toContainText("Authorization active");
  await expect(page.getByTestId("countdown")).toHaveText(/00:(2\d|1\d|0[1-9])/);
  await expect(page.getByTestId("connectors").locator('path[data-causal="true"]')).toHaveCount(3);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${SHOTS}/B-authorized.png` });

  // Execution is a separate, explicit act (Document 05 §116).
  await page.getByTestId("execute").click();
  await expect(page).toHaveURL(/\/execution\/dec_/);
  await expect(page.getByTestId("executed")).toBeVisible();
  await expect(page.getByTestId("sequence").locator('li[data-state="done"]')).toHaveCount(4);
  await expect(page.getByTestId("monad-proof")).toContainText("Receipt committed");

  await page.getByRole("link", { name: "Open receipt" }).click();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Why did this action happen?");
  await expect(page.getByTestId("timeline")).toContainText("Protected action executed");
  await page.getByTestId("replay-button").click();
  await expect(page.getByTestId("replay-result")).toHaveText("Deterministic replay verified");
  await page.getByRole("button", { name: "Test receipt integrity" }).click();
  await expect(page.getByTestId("tamper")).toContainText("Mismatch");
  await expect(page.getByTestId("tamper")).toContainText("Invalid");
  await expect(page.getByTestId("compare")).toContainText("One evidence change. One authority change.");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${SHOTS}/C-receipt.png`, fullPage: true });
});
