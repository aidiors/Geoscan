import { expect, test } from "@playwright/test";

test("can stop after the first audited result and keep its export", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Бюджет расчёта, мин", { exact: true }).fill("30");
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Расчётный план" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Остановить поиск" })).toBeVisible();
  await page.getByRole("button", { name: "Остановить поиск" }).click();
  await expect(page.getByRole("status")).toContainText("остановлен");
  await expect(page.getByRole("heading", { name: "Расчётный план" })).toBeVisible();
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await expect(page.getByRole("button", { name: "GeoJSON", exact: true })).toBeEnabled();
  await expect(page.getByRole("region", { name: "История улучшений плана" })).toContainText("Поиск остановлен");
  const exported = page.waitForResponse((r) => /\/v1\/exports\/(qgroundcontrol|flight-package)$/.test(r.url()));
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "QGroundControl", exact: true }).click();
  expect((await exported).status()).toBe(200);
  await downloaded;
});

test("demonstrates a joint sortie with a nonzero turn radius", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Демо-кейсы", exact: true }).click();
  await page.getByRole("button", { name: /Один вылет — несколько участков/ }).click();
  await page.getByRole("button", { name: "Загрузить и рассчитать", exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await expect(page.getByText("Вылетов по нескольким областям: 1", { exact: true })).toBeVisible();
  await expect(page.locator(".missions-heading small")).toContainText("1 вылетов");
  await expect(page.getByRole("region", { name: "История улучшений плана" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("joint-fields.png") });
});
