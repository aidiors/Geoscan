import { expect, test } from "@playwright/test";

test("keeps imported inputs separate from demo restrictions and preserves unknown obstacle elevation", async ({ page }) => {
  await page.goto("/");
  const field = { type: "FeatureCollection", features: [{ type: "Feature", properties: { name: "Own field" },
    geometry: { type: "Polygon", coordinates: [[[37.394, 55.692], [37.406, 55.692], [37.406, 55.696], [37.394, 55.696], [37.394, 55.692]]] },
  }] };
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name: "own-field.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(field)) });
  await page.getByRole("button", { name: "Импортировать зону" }).click();
  await expect(page.getByText("Рабочие области взяты из импорта", { exact: false })).toBeVisible();
  await page.getByLabel("Тип импортируемых геоданных").selectOption("obstacle");
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name: "unknown-top.kml", mimeType: "application/vnd.google-earth.kml+xml",
    buffer: Buffer.from('<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>Unknown top</name><Point><coordinates>37.5,55.8</coordinates></Point></Placemark></kml>') });
  await page.getByRole("button", { name: "Импортировать зону" }).click();
  await expect(page.getByText("Препятствий без абсолютной отметки вершины: 1", { exact: false })).toBeVisible();
  const pending = page.waitForResponse((response) => response.url().endsWith("/v1/plans") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  const response = await pending;
  const request = response.request().postDataJSON();
  expect(request.no_fly_zones).toEqual([]);
  expect(request.coverage_areas).toHaveLength(1);
  expect(request.obstacles[0].height_m).toBeNull();
  expect(request.obstacles[0].altitude_mode).toBe("unknown");
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await page.getByText("Допущения и границы проверки", { exact: true }).click();
  await expect(page.getByText("Для препятствий без абсолютной отметки вершины MSL", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Очистить зоны", exact: true }).click();
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Выберите хотя бы одну рабочую область");
});
