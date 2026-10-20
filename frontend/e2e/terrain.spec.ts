import { expect, test } from "@playwright/test";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";

test("loads a DEM, plans AGL, displays the profile and restores the exact raster", async ({ page }, testInfo) => {
  await page.goto("/");
  const field = JSON.stringify({ type: "FeatureCollection", features: [{ type: "Feature", properties: { name: "DEM test field" },
    geometry: { type: "Polygon", coordinates: [[[37.394, 55.692], [37.406, 55.692], [37.406, 55.696], [37.394, 55.696], [37.394, 55.692]]] },
  }] });
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name: "field.geojson", mimeType: "application/geo+json", buffer: Buffer.from(field) });
  await page.getByRole("button", { name: "Импортировать зону" }).click();
  await expect(page.getByText("Используется собственная геометрия задания", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: /Управлять парком/ }).click();
  for (const name of ["Геоскан 201", "Геоскан 801"]) {
    await page.locator(".fleet-row").filter({ hasText: name }).click();
    await page.getByRole("checkbox", { name: "Использовать в расчёте", exact: true }).uncheck();
  }
  await page.locator(".fleet-row").filter({ hasText: "Геоскан Gemini" }).click();
  await page.getByRole("button", { name: "Готово", exact: true }).click();
  await page.getByLabel("Режим высоты", { exact: true }).selectOption("agl");
  await page.getByLabel("Высота над рельефом AGL, м", { exact: true }).fill("100");
  await page.getByLabel("Погрешность DEM ±, м", { exact: true }).fill("3");
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("подтвердите");
  await page.locator('input[accept=".tif,.tiff"]').setInputFiles(resolve("frontend/e2e/fixtures/synthetic-terrain.tif"));
  await expect(page.getByRole("status")).toContainText("DEM загружен");
  await page.getByRole("checkbox", { name: "Подтверждаю: высоты после scale/offset заданы в метрах", exact: true }).check();
  await page.getByRole("checkbox", { name: "Подтверждаю: отметки DEM приведены к используемой системе MSL", exact: true }).check();
  await page.getByRole("checkbox", { name: /Быстрый расчёт/ }).check();
  const planned = page.waitForResponse((r) => r.url().endsWith("/v1/plans"));
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await planned;
  await expect(page.getByRole("img", { name: "Профиль DEM и высоты MSL по расстоянию", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Проверить диапазон", exact: true })).toBeDisabled();
  await expect(page.getByText(/Загруженный GeoTIFF автоматически/)).toBeVisible();
  await page.getByText("Проекты и история", { exact: true }).click();
  const name = `Terrain ${randomUUID()}`;
  await page.getByLabel("Название проекта", { exact: true }).fill(name);
  const saved = page.waitForResponse((r) => r.url().endsWith("/v1/projects") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Сохранить новый проект", exact: true }).click();
  const response = await saved;
  expect(response.status()).toBe(200);
  const project = await response.json();
  expect(project.document.editor_state.terrain.content).toBe(project.document.request.terrain_source.content);
  expect(project.document.request.altitude_reference).toBe("agl");
  expect(project.document.plan.drone_plans.flatMap((d: { missions: { vertical_profile: unknown[] }[] }) => d.missions).some((m: { vertical_profile: unknown[] }) => m.vertical_profile.length > 0)).toBe(true);
  await page.reload();
  await page.getByText("Проекты и история", { exact: true }).click();
  await page.getByLabel("Сохранённые проекты", { exact: true }).selectOption(project.project_id);
  await expect(page.getByLabel("Ревизия проекта", { exact: true })).toContainText("1 ·");
  await page.getByRole("button", { name: "Открыть ревизию", exact: true }).click();
  await expect(page.getByLabel("Режим высоты", { exact: true })).toHaveValue("agl");
  await expect(page.getByRole("checkbox", { name: /отметки DEM приведены/ })).toBeChecked();
  await expect(page.getByRole("img", { name: "Профиль DEM и высоты MSL по расстоянию", exact: true })).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "GeoJSON", exact: true }).click();
  await (await downloaded).saveAs(testInfo.outputPath("terrain-mission.geojson"));
  await page.screenshot({ path: testInfo.outputPath("terrain-restored.png") });
});
