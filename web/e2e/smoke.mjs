// Phase 6 smoke path: staff -> Simulator -> glass break -> alert live -> detail -> acknowledge -> resolve.
// Usage (app running): node e2e/smoke.mjs [baseUrl]
import { chromium } from "playwright";

const BASE = process.argv[2] || "http://127.0.0.1:5173";
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const step = (s) => console.log("✓", s);
try {
  await page.goto(BASE);
  await page.getByRole("button", { name: /Sign in as Staff/ }).click();
  await page.getByRole("heading", { name: /Alerts/ }).waitFor();
  step("signed in as staff");

  // A second tab watching the alert list proves live (WebSocket) updates.
  const watcher = await context.newPage();
  await watcher.goto(BASE + "/alerts");
  const before = await watcher.locator("tbody tr.clickable").count();

  await page.getByRole("link", { name: "Operations" }).click();
  await page.locator('[data-scenario="glass"]').click();
  await page.getByTestId("ai-explanation").waitFor();
  step("glass break sent, alert detail opened");
  const explanation = await page.getByTestId("ai-explanation").innerText();
  if (!/llama3\.2:3b|qwen/.test(explanation)) throw new Error("model not shown in AI explanation: " + explanation);
  step("AI explanation shows model + prompt: " + explanation.replace(/\s+/g, " ").slice(0, 120));
  await page.getByTestId("timeline").getByText("Maria Lopez").first().waitFor();
  step("timeline shows notifications to Maria Lopez");

  await watcher.waitForFunction((n) => document.querySelectorAll("tbody tr.clickable").length > n, before, { timeout: 5000 });
  step("alert appeared live in another tab without reload");

  await page.getByRole("button", { name: "Acknowledge" }).click();
  await page.locator(".chip.acknowledged").first().waitFor();
  step("acknowledged");
  await page.getByRole("button", { name: "Resolve" }).click();
  await page.locator(".chip.resolved").first().waitFor();
  if (!(await page.getByRole("button", { name: "Acknowledge" }).isDisabled())) throw new Error("closed alert should be read-only");
  step("resolved; actions disabled (read-only)");

  await page.reload();
  await page.locator(".chip.resolved").first().waitFor();
  step("deep link reload renders the SPA (not API JSON)");
  console.log("SMOKE PASSED");
} catch (e) {
  await page.screenshot({ path: "e2e/smoke-failure.png", fullPage: true });
  console.error("SMOKE FAILED:", e.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
