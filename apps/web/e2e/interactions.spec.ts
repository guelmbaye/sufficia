import { expect, type Page, test } from "@playwright/test";

/* Everything a presenter or a judge can do in the demo, each in a real browser against the real API:
   every button, link, drawer, shortcut and copy action, plus double clicks, reloads and back/forward. */

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

const GP = "Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K.";

/** Every non-GET call the page makes to the API, so tests can assert what was (not) written. */
function recordWrites(page: Page): string[] {
  const writes: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/v1/") && r.method() !== "GET") writes.push(`${r.method()} ${new URL(r.url()).pathname}`);
  });
  return writes;
}

const run = (page: Page) => page.evaluate(() => JSON.parse(window.localStorage.getItem("sufficia.run.v2") ?? "{}"));
const stepper = (page: Page) => page.getByRole("navigation", { name: "Run steps" });

test("mission: instruction editing, refusals and warnings", async ({ page }) => {
  await page.goto("/");
  const input = page.getByLabel("Instruction");
  const compile = page.getByRole("button", { name: "Compile Evidence Contract" });
  await expect(page.getByText(/Understood as BUY TOKEN-X with 3 mandatory/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Use Grand Prize instruction" })).toHaveCount(0);

  await input.fill("Buy up to 20 USDC of TOKEN-X only if liquidity exceeds $300K");
  await expect(page.getByText(/with 2 mandatory requirements/)).toBeVisible();
  await page.getByRole("button", { name: "Use Grand Prize instruction" }).click();
  await expect(input).toHaveValue(GP);

  await input.fill("");
  await expect(compile).toBeDisabled();
  await input.fill("hello world, do something nice");
  await expect(page.getByText(/Could not find: action/)).toBeVisible();
  await expect(compile).toBeDisabled();

  await input.fill("Acheter jusqu'à 100 USDC de TOKEN-X seulement si la liquidité dépasse 500 000 $");
  await expect(page.getByText(/available liquidity more than \$500,000/)).toBeVisible();

  await input.fill("Sell up to 50 USDC of TOKEN-X only if liquidity exceeds $500K");
  await expect(page.getByTestId("not-executable")).toContainText("only performs BUY actions");

  // digits typed in the instruction are text, not shortcuts
  await input.fill("");
  await input.type("Buy up to 13 USDC of TOKEN-X only if liquidity exceeds $2M");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("proof drawer: button, close, overlay, shortcut 3", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("chain-badge")).toContainText("Local EVM");
  const drawer = page.getByRole("dialog", { name: "Proof" });
  await page.getByRole("button", { name: "Proof" }).click();
  await expect(drawer).toContainText("Benchmark cases matched");
  await drawer.getByRole("button", { name: "Close", exact: true }).last().click();
  await expect(drawer).toHaveCount(0);
  await page.getByRole("button", { name: "Proof" }).click();
  await page.mouse.click(40, 500); // the overlay
  await expect(drawer).toHaveCount(0);
  await page.getByRole("heading", { level: 1 }).click();
  await page.keyboard.press("3");
  await expect(drawer).toBeVisible();
  await page.keyboard.press("3");
  await expect(drawer).toHaveCount(0);
});

test("custom mission: contract tools, acquisition, back, manual execution from the stepper", async ({ page }) => {
  const writes = recordWrites(page);
  await page.goto("/");
  await page.getByLabel("Instruction").fill("Buy up to 13 USDC of TOKEN-X only if liquidity exceeds $2M");
  await expect(page.getByText(/with 2 mandatory requirements/)).toBeVisible();
  await page.getByRole("button", { name: "Compile Evidence Contract" }).click();
  await expect(page.getByRole("heading", { name: "What must be true before money can move?" })).toBeVisible();

  await page.getByText("View raw contract").click();
  await expect(page.locator("pre")).toContainText('"contract_id"');
  await page.getByRole("button", { name: /^Copy 0x/ }).first().click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^0x[0-9a-f]{64}$/);

  await page.getByRole("link", { name: "Acquire evidence" }).click();
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  await expect(page).not.toHaveURL(/acquire/);

  const acquisitions = writes.filter((w) => w.endsWith("/acquire")).length;
  await page.goBack();
  await expect(page.getByRole("link", { name: "Open the live gate" })).toBeVisible();
  await page.getByRole("link", { name: "Open the live gate" }).click();
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  expect(writes.filter((w) => w.endsWith("/acquire")).length).toBe(acquisitions);

  await page.getByTestId("condition-valid").click();
  await expect(page.getByTestId("countdown")).toBeVisible();
  await stepper(page).getByRole("link", { name: /Execution/ }).click();
  await page.getByTestId("execute-now").click();
  await expect(page.getByTestId("executed")).toBeVisible();
  await page.getByRole("link", { name: "Open receipt" }).first().click();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Why did this action happen?");
  await expect(page.locator("main")).toContainText("13 USDC");
});

test("gate: inspectors and the recovery action of every state", async ({ page }) => {
  await page.goto("/demo");
  const gate = page.getByTestId("gate");
  await expect(gate).toHaveAttribute("data-state", "DENY");

  for (const id of ["req_inflow", "req_liquidity", "req_price"]) {
    await page.getByTestId(`row-${id}`).getByRole("button", { name: "Details" }).click();
    const d = page.getByRole("dialog");
    await expect(d).toContainText("Response hash");
    await d.getByText("Canonical JSON").click();
    await expect(d.locator("pre")).toContainText('"evidence_id"');
    await page.keyboard.press("Escape");
    await expect(d).toHaveCount(0);
  }

  await page.getByTestId("condition-stale").click();
  await expect(gate).toHaveAttribute("data-state", "REFRESH");
  await page.getByRole("button", { name: "Refresh evidence" }).click();
  await expect(gate).toHaveAttribute("data-state", "AUTHORIZE");

  await page.getByTestId("condition-missing").click();
  await expect(gate).toHaveAttribute("data-state", "ABSTAIN");
  await page.getByRole("button", { name: "Acquire evidence again" }).click();
  await expect(gate).toHaveAttribute("data-state", "AUTHORIZE");

  await page.getByTestId("condition-conflict").click();
  await expect(gate).toHaveAttribute("data-state", "ESCALATE");
  await page.getByRole("button", { name: "Inspect conflict" }).click();
  await expect(page.getByRole("dialog").getByText("Response hash")).toHaveCount(2); // both sources
  await page.mouse.click(40, 500);
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.getByTestId("condition-threshold").click();
  await expect(gate).toHaveAttribute("data-state", "DENY");
  await page.getByRole("link", { name: "Open receipt" }).click();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Blocked action receipt");
  await expect(page.getByTestId("receipt-callout")).toContainText("$92,440");
  await page.getByTestId("replay-button").click();
  await expect(page.getByTestId("replay-result")).toHaveText("Deterministic replay verified");
  await page.getByRole("link", { name: "Back to the live gate" }).click();
  await expect(gate).toHaveAttribute("data-state", "DENY");
});

test("double clicks, reloads and back/forward never act twice", async ({ page }) => {
  const writes = recordWrites(page);
  await page.goto("/demo");
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");

  let n = writes.length;
  await page.getByTestId("condition-valid").dblclick();
  await expect(page.getByTestId("countdown")).toBeVisible();
  expect(writes.slice(n).filter((w) => w.includes("/test/"))).toHaveLength(1);

  n = writes.length;
  await page.getByTestId("execute").dblclick();
  await expect(page.getByTestId("executed")).toBeVisible();
  await page.waitForTimeout(500);
  expect(writes.slice(n).filter((w) => w.endsWith("/execute"))).toHaveLength(1);

  n = writes.length;
  await page.reload();
  await expect(page.getByTestId("executed")).toBeVisible();
  await page.goBack();
  await expect(page.getByTestId("decision-headline")).toContainText("Executed");
  await page.goForward();
  await expect(page.getByTestId("executed")).toBeVisible();
  expect(writes.slice(n)).toEqual([]);
});

test("receipt tools, comparison links, shortcuts and every step of the stepper", async ({ page }) => {
  await page.goto("/demo");
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  await page.getByTestId("condition-valid").click();
  await page.getByTestId("execute").click();
  await expect(page.getByTestId("executed")).toBeVisible();
  await page.getByRole("link", { name: "Open receipt" }).first().click();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Why did this action happen?");

  await page.getByTestId("replay-button").click();
  await expect(page.getByTestId("replay-result")).toHaveText("Deterministic replay verified");
  await page.getByTestId("replay-button").click();
  await expect(page.getByTestId("replay-button")).toHaveText("Replay decision again");
  await page.getByRole("button", { name: "Show all checks" }).click();
  await expect(page.getByTestId("replay").locator("table tr")).toHaveCount(24);
  await page.getByRole("button", { name: "Hide all checks" }).click();
  await page.getByRole("button", { name: "Test receipt integrity" }).click();
  await expect(page.getByTestId("tamper")).toContainText("Invalid");
  await page.getByRole("button", { name: "Copy verification data" }).click();
  await expect(page.getByRole("button", { name: "Verification data copied" })).toBeVisible();
  const copied = JSON.parse(await page.evaluate(() => navigator.clipboard.readText()));
  expect(copied.receipt_hash).toMatch(/^0x[0-9a-f]{64}$/);
  expect(copied.execution_tx).toMatch(/^0x/);

  const links = page.getByTestId("compare").getByRole("link", { name: "Open receipt" });
  await expect(links).toHaveCount(2);
  await links.first().click();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Blocked action receipt");
  await page.goBack();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Why did this action happen?");

  await page.getByRole("heading", { level: 1 }).click();
  await page.keyboard.press("1");
  await expect(page.getByTestId("gate")).toBeVisible();
  await page.keyboard.press("2");
  await expect(page.getByTestId("receipt-headline")).toBeVisible();

  for (const [label, url] of [
    ["Mission", /\/$/],
    ["Evidence Contract", /\/contract\//],
    ["Live gate", /\/gate\//],
    ["Execution", /\/execution\//],
    ["Receipt", /\/receipt\//],
  ] as const) {
    await stepper(page).getByRole("link", { name: new RegExp(label) }).click();
    await expect(page).toHaveURL(url);
  }
});

test("an intent's capital moves once: later AUTHORIZE evidence gets no second authorization", async ({ page }) => {
  const writes = recordWrites(page);
  await page.goto("/demo");
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  const { contractId } = await run(page);
  await page.getByTestId("condition-valid").click();
  await page.getByTestId("execute").click();
  await expect(page.getByTestId("executed")).toBeVisible();

  await page.goto(`/gate/${contractId}`);
  const n = writes.length;
  await page.getByTestId("condition-valid").click();
  await expect(page.getByTestId("decision-headline")).toHaveText("Already executed");
  await expect(page.getByTestId("execute")).toHaveCount(0);
  await expect(page.getByTestId("protected-capital")).toContainText("Executed");
  await page.waitForTimeout(500);
  expect(writes.slice(n).filter((w) => w.includes("/authorize") || w.endsWith("/execute"))).toEqual([]);

  await stepper(page).getByRole("link", { name: /Execution/ }).click();
  await expect(page.getByRole("heading", { name: "This intent already executed" })).toBeVisible();
  await page.getByRole("link", { name: "Open receipt" }).click();
  await expect(page.getByTestId("receipt-headline")).toHaveText("Why did this action happen?");

  await page.goto(`/gate/${contractId}`);
  await page.getByTestId("condition-threshold").click();
  await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
  await expect(page.getByTestId("consequence")).toContainText("already moved once");
});

test("an action this server cannot execute is said up front and never authorized", async ({ page }) => {
  const writes = recordWrites(page);
  await page.goto("/");
  await page.getByLabel("Instruction").fill("Sell up to 50 USDC of TOKEN-X only if liquidity exceeds $500K");
  await expect(page.getByTestId("not-executable")).toBeVisible();
  await page.getByRole("button", { name: "Compile Evidence Contract" }).click();
  await expect(page.getByTestId("not-executable")).toBeVisible();
  await page.getByRole("link", { name: "Acquire evidence" }).click();
  await expect(page.getByTestId("gate")).toBeVisible();
  await page.getByTestId("condition-valid").click();
  await expect(page.getByTestId("decision-headline")).toHaveText("Not executable here");
  await expect(page.getByTestId("execute")).toHaveCount(0);
  await stepper(page).getByRole("link", { name: /Execution/ }).click();
  await expect(page.getByRole("heading", { name: "Not executable here" })).toBeVisible();
  expect(writes.filter((w) => w.includes("/authorize"))).toEqual([]);
});

test("unknown records and routes lead back to the mission", async ({ page }) => {
  await page.goto("/receipt/rcpt_nope");
  await expect(page.getByRole("heading", { name: "Receipt not found" })).toBeVisible();
  await page.getByRole("link", { name: "Start from the mission" }).click();
  await expect(page.getByRole("heading", { name: "What should the agent do?" })).toBeVisible();
  for (const [url, heading] of [
    ["/gate/ec_nope", "Evidence Contract not found"],
    ["/contract/ec_nope", "Evidence Contract not found"],
    ["/execution/dec_nope", "Decision not found"],
  ]) {
    await page.goto(url);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  }
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("demo, valid, execute, receipt and proof by touch", async ({ page }) => {
    await page.goto("/demo");
    await expect(page.getByTestId("gate")).toHaveAttribute("data-state", "DENY");
    await page.getByTestId("condition-valid").tap();
    await expect(page.getByTestId("countdown")).toBeVisible();
    await page.getByTestId("execute").tap();
    await expect(page.getByTestId("executed")).toBeVisible();
    await page.getByRole("link", { name: "Open receipt" }).first().tap();
    await expect(page.getByTestId("receipt-headline")).toBeVisible();
    await page.getByRole("button", { name: "Proof" }).tap();
    await expect(page.getByRole("dialog", { name: "Proof" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
