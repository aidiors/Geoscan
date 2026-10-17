import { expect, test } from "@playwright/test";

test("compares productive routes with the reference pipeline through the real UI", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Демо-кейсы" }).click();
  await page.getByRole("button", { name: /Сокращение холостого налёта/ }).click();
  const firstResponse = page.waitForResponse((response) => response.url().endsWith("/v1/plans") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Загрузить и рассчитать" }).click();
  const productive = await (await firstResponse).json();
  expect(productive.optimization.productive_routing.selected_variant).not.toBe("baseline");
  expect(productive.safety_audit.status).toBe("passed");
  await expect(page.getByText("Сравнение вылетов", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Оптимизация вылетов/ })).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("productive-routes.png"), fullPage: true });

  await page.getByRole("checkbox", { name: /Оптимизация вылетов/ }).uncheck();
  const referenceResponse = page.waitForResponse((response) => response.url().endsWith("/v1/plans") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Рассчитать/ }).click();
  const reference = await (await referenceResponse).json();
  expect(reference.optimization.productive_routing).toBeNull();
  expect(productive.metrics.total_flight_minutes).toBeLessThan(reference.metrics.total_flight_minutes);
  expect(productive.metrics.sortie_count).toBeLessThan(reference.metrics.sortie_count);
});

test("opens the curated case gallery and loads a group mission", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Городская ортофотосъёмка" })).toBeVisible();
  await page.getByRole("button", { name: "Демо-кейсы" }).click();
  await expect(page.getByRole("dialog", { name: "Витрина возможностей GeoPlan" })).toBeVisible();

  await page.getByRole("button", { name: /Группа БВС над кварталами/ }).click();
  await expect(page.getByRole("heading", { name: "Группа БВС над кварталами" })).toBeVisible();
  await page.getByRole("button", { name: "Только загрузить" }).click();

  await expect(page.getByRole("heading", { name: "Несколько городских кварталов" })).toBeVisible();
  await expect(page.getByText("Три рабочих района распределяются по парку", { exact: false })).not.toBeVisible();
});

test("runs a real plan from a demo case and exposes its safety evidence", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Демо-кейсы" }).click();
  await page.getByRole("button", { name: /Безопасная городская съёмка/ }).click();
  await page.getByRole("button", { name: "Загрузить и рассчитать" }).click();

  await expect(page.getByRole("heading", { name: "План готов" })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Независимый аудит плана")).toBeVisible();
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await expect(page.getByRole("button", { name: /GeoJSON/ })).toBeEnabled();
});

test("shows an actionable refusal for unsafe weather", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Демо-кейсы" }).click();
  await page.getByRole("button", { name: /Управляемый отказ: шторм/ }).click();
  await page.getByRole("button", { name: "Загрузить и рассчитать" }).click();

  await expect(page.getByRole("status")).toContainText(/ветер|ветра|БВС/i, { timeout: 120_000 });
  await expect(page.getByRole("heading", { name: "Ожидает расчёта" })).toBeVisible();
});
