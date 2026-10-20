import { expect, test } from "@playwright/test";

test("compares productive routes with the reference pipeline through the real UI", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Демо-кейсы" }).click();
  await page.getByRole("button", { name: /Сокращение холостого налёта/ }).click();
  const firstResponse = page.waitForResponse((response) => response.url().endsWith("/v1/plans") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Загрузить и рассчитать" }).click();
  await firstResponse;
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  const productiveFlight = parseFloat((await page.locator(".metric").filter({hasText:"Налёт"}).locator("strong").innerText()).replace(/\s/g, "").replace(",", "."));
  const productiveSorties = parseInt(await page.locator(".missions-heading small").innerText());
  await expect(page.getByText("Сравнение вылетов", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Оптимизация вылетов/ })).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("productive-routes.png"), fullPage: true });

  await page.getByRole("checkbox", { name: /Оптимизация вылетов/ }).uncheck();
  const referenceResponse = page.waitForResponse((response) => response.url().endsWith("/v1/plans") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Рассчитать/ }).click();
  await referenceResponse;
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("Сравнение вылетов", { exact: true })).not.toBeVisible();
  const referenceFlight = parseFloat((await page.locator(".metric").filter({hasText:"Налёт"}).locator("strong").innerText()).replace(/\s/g, "").replace(",", "."));
  const referenceSorties = parseInt(await page.locator(".missions-heading small").innerText());
  expect(productiveFlight).toBeLessThan(referenceFlight);
  expect(productiveSorties).toBeLessThan(referenceSorties);
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

  await expect(page.getByRole("heading", { name: "Расчётный план" })).toBeVisible({ timeout: 120_000 });
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

test("inspects real frame footprints and applies a replanned deadline alternative", async ({ page }, testInfo) => {
  await page.goto("/");
  const area = { type: "FeatureCollection", features: [{ type: "Feature",
    properties: { name: "Quality test field" },
    geometry: { type: "Polygon", coordinates: [[[37.394, 55.692], [37.406, 55.692], [37.406, 55.696], [37.394, 55.696], [37.394, 55.692]]] },
  }] };
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name: "quality-field.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(area)) });
  const imported = page.waitForResponse((response) => response.url().endsWith("/v1/imports/geojson"));
  await page.getByRole("button", { name: "Импортировать зону" }).click();
  expect((await imported).status()).toBe(200);
  await page.getByLabel("Срок, мин", { exact: true }).fill("1");
  await page.getByRole("button", { name: /Рассчитать план/ }).click();
  await expect(page.getByRole("heading", { name: "Расчётный план" })).toBeVisible();
  const qualityResponse = page.waitForResponse((response) => response.url().endsWith("/v1/survey-quality"));
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await page.getByRole("button", { name: "Проверить отпечатки кадров" }).click();
  const quality = await (await qualityResponse).json();
  expect(quality.frame_count).toBeGreaterThan(0);
  expect(quality.covered_percent).toBeGreaterThan(99);
  await expect(page.getByText(/Кадров:/)).toBeVisible();
  await expect(page.locator(".quality-layer path").first()).toBeAttached();
  await page.screenshot({ path: testInfo.outputPath("survey-quality.png"), fullPage: true });

  const adviceResponse = page.waitForResponse((response) => response.url().endsWith("/v1/feasibility-advice"));
  await page.getByRole("button", { name: "Подобрать условия выполнения" }).click();
  const advice = await (await adviceResponse).json();
  const verified = advice.variants.find((variant: { status: string }) => variant.status === "verified");
  expect(verified).toBeTruthy();
  await page.getByRole("button", { name: "Применить проверенный вариант" }).first().click();
  await expect(page.getByRole("heading", { name: "Расчётный план" })).toBeVisible();
  await expect(page.getByLabel("Срок, мин", { exact: true })).toHaveValue(String(verified.request.completion_deadline_minutes));
  await expect(page.getByRole("button", { name: /GeoJSON/ })).toBeEnabled();
});
