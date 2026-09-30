import { expect, test } from "@playwright/test";

/* Document 05 §25-26: each test condition produces its own deterministic outcome, in any order. */
test("test conditions produce the five states in any order", async ({ page }) => {
  await page.goto("/demo");
  const gate = page.getByTestId("gate");
  await expect(gate).toHaveAttribute("data-state", "DENY");

  const expectations: [string, string, string][] = [
    ["stale", "REFRESH", "Evidence expired"],
    ["conflict", "ESCALATE", "Human review required"],
    ["missing", "ABSTAIN", "Agent abstained"],
    ["valid", "AUTHORIZE", "Execution authorized"],
    ["threshold", "DENY", "Execution denied"],
  ];
  for (const [condition, state, headline] of expectations) {
    await page.getByTestId(`condition-${condition}`).click();
    await expect(gate).toHaveAttribute("data-state", state);
    await expect(page.getByTestId("decision-headline")).toHaveText(headline);
    if (state !== "AUTHORIZE") await expect(page.getByTestId("execute-disabled")).toBeDisabled();
  }
});

test("stale evidence explains its age and offers a refresh that restores authority", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  await page.getByTestId("condition-stale").click();
  await expect(page.getByTestId("row-req_price")).toHaveAttribute("data-status", "STALE");
  await expect(page.getByTestId("row-req_price")).toContainText("Maximum age 30 s");
  await page.getByRole("button", { name: "Refresh evidence" }).click();
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "AUTHORIZE");
});

test("a conflict is inspectable and never executable", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  await page.getByTestId("condition-conflict").click();
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "ESCALATE");
  await expect(page.getByTestId("row-req_liquidity")).toContainText("Source B");
  await expect(page.getByRole("button", { name: /Execute anyway/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Inspect conflict" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("CRITICAL_SOURCE_CONFLICT");
  await expect(drawer).toContainText("Response hash");
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
});

test("the API refuses execution of a denied decision even when the UI is bypassed", async ({ page, request }) => {
  await page.goto("/demo");
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  const run = await page.evaluate(() => JSON.parse(window.localStorage.getItem("sufficia.run.v1") ?? "{}"));
  const res = await request.post(`/api/v1/decisions/${run.decisionId}/execute`);
  expect(res.status()).toBe(403);
  expect((await res.json()).error.code).toBe("EVIDENCE_AUTHORIZATION_REQUIRED");
});
