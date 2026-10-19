import { expect, test } from "@playwright/test";

test("plans survey height above an explicit surface while exporting absolute MSL", async ({ page }) => {
  await page.goto("/");
  const field = { type: "FeatureCollection", features: [{ type: "Feature", properties: { name: "Elevated field" },
    geometry: { type: "Polygon", coordinates: [[[37.394, 55.692], [37.406, 55.692], [37.406, 55.696], [37.394, 55.696], [37.394, 55.692]]] },
  }] };
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name: "elevated-field.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(field)) });
  await page.getByRole("button", { name: "Импортировать зону" }).click();
  await expect(page.getByText("Рабочие области взяты из импорта", { exact: false })).toBeVisible();
  await page.getByLabel("Высота задания MSL, м", { exact: true }).fill("250");
  await page.getByLabel("Отметка поверхности MSL, м", { exact: true }).fill("150");
  await expect(page.getByText("Высота над поверхностью: 100 м", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await page.getByText("Допущения и границы проверки", { exact: true }).click();
  await expect(page.getByText("плоская поверхность 150 м MSL", { exact: false })).toBeVisible();
  const qualityPending = page.waitForResponse((r) => r.url().endsWith("/v1/survey-quality"));
  await page.getByRole("button", { name: "Проверить отпечатки кадров" }).click();
  const quality = await (await qualityPending).json();
  expect(quality.covered_percent).toBeGreaterThan(99);
  expect(quality.maximum_gsd_cm).toBeLessThanOrEqual(5);
  const exportPending = page.waitForResponse((r) => r.url().endsWith("/v1/exports/geojson"));
  await page.getByRole("button", { name: "GeoJSON", exact: true }).click();
  const exported = await (await exportPending).json();
  expect(exported.features.length).toBeGreaterThan(0);
  expect(exported.features[0].properties.altitude_m).toBe(250);
  expect(exported.features[0].properties.flat_ground_elevation_m).toBe(150);
});
