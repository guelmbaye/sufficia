import { expect, test } from "@playwright/test";

test("mission to contract to live gate with a custom instruction", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What should the agent do?" })).toBeVisible();
  const input = page.getByLabel("Instruction");
  await input.fill("Buy up to 50 USDC of TOKEN-X only if liquidity exceeds $2M");
  await expect(page.getByText(/Understood as BUY TOKEN-X with 2 mandatory requirements/)).toBeVisible();
  await page.getByRole("button", { name: "Compile Evidence Contract" }).click();

  await expect(page.getByRole("heading", { name: "What must be true before money can move?" })).toBeVisible();
  await expect(page.getByText("Intent preserved")).toBeVisible();
  await expect(page.getByText("more than $2,000,000").first()).toBeVisible();
  await expect(page.getByText("50 USDC").first()).toBeVisible();
  await page.getByRole("link", { name: "Acquire evidence" }).click();

  // every simulated market state (baseline $92,440, test-lab $718,000) is below $2,000,000
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  await expect(page.getByTestId("consequence")).toContainText("50 USDC did not move");
});

test("unknown words are shown as not enforced, never silently dropped", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Instruction").fill("Buy up to 100 USDC of TOKEN-X only if liquidity exceeds $500K and RSI is below 30");
  await expect(page.getByText(/Not understood, so not enforced/)).toBeVisible();
});

test("proof drawer opens with the keyboard and shows only verified numbers", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("chain-badge")).toContainText("Local EVM");
  await page.getByRole("heading", { level: 1 }).click();
  await page.keyboard.press("3");
  const drawer = page.getByRole("dialog", { name: "Proof" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("Benchmark cases matched");
  await expect(drawer).toContainText("50 of 50");
  await expect(drawer).toContainText("Local EVM rehearsal (not Monad)");
});

test.describe("phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("mission and live gate have no horizontal scroll", async ({ page }) => {
    const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
    await page.goto("/");
    await expect(page.getByText(/Understood as/)).toBeVisible();
    expect(await noOverflow()).toBe(true);
    await page.goto("/demo");
    await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
    expect(await noOverflow()).toBe(true);
  });
});
