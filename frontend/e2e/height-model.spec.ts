import { expect, test } from "@playwright/test";
import { flightPackageFiles } from "./flightPackage";
import { readFile } from "node:fs/promises";

test("plans survey height above an explicit surface while exporting absolute MSL", async ({ page }, testInfo) => {
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
  const autopilotPending = page.waitForResponse((r) => /\/v1\/exports\/(qgroundcontrol|flight-package)$/.test(r.url()));
  const autopilotDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "QGroundControl", exact: true }).click();
  const exportResponse = await autopilotPending;
  expect(exportResponse.status()).toBe(200);
  let autopilot;
  if (exportResponse.url().endsWith("flight-package")) {
    const downloadedPath = testInfo.outputPath("flight-package.zip");
    await (await autopilotDownload).saveAs(downloadedPath);
    const files = flightPackageFiles(await readFile(downloadedPath));
    const manifest = JSON.parse(files.get("manifest.json")!.toString());
    expect(manifest.sorties.length).toBeGreaterThan(1);
    expect(manifest.execution_policy).toBe("operator_dispatch_each_sortie");
    for (const sortie of manifest.sorties) {
      const items = JSON.parse(files.get(sortie.file)!.toString()).mission.items;
      expect(items.filter((item: { command: number }) => item.command === 22)).toHaveLength(1);
      expect(items.filter((item: { command: number }) => item.command === 21)).toHaveLength(1);
    }
    autopilot = JSON.parse(files.get(manifest.sorties[0].file)!.toString()).mission;
  } else {
    await autopilotDownload;
    autopilot = JSON.parse((await exportResponse.json()).content).mission;
  }
  expect(autopilot.plannedHomePosition[2]).toBe(150);
  expect(autopilot.globalPlanAltitudeMode).toBe(2);
  const positions = autopilot.items.filter((item: { command: number }) => [16, 21, 22].includes(item.command));
  expect(positions.length).toBeGreaterThan(0);
  for (const item of positions) {
    expect(item.frame).toBe(0);
    expect(item.AltitudeMode).toBe(2);
    expect(item.params[6]).toBe(item.command === 21 ? 150 : 250);
  }
  await page.screenshot({ path: testInfo.outputPath("flight-release.png") });
  for (const name of ["Геоскан 201", "Геоскан 801"]) {
    await page.locator(".fleet-row").filter({ hasText: name }).click();
    await page.getByLabel("Использовать в расчёте", { exact: true }).uncheck();
  }
  await page.locator(".fleet-row").filter({ hasText: "Геоскан Gemini" }).click();
  await page.getByLabel("Учитывать вертикальные взлёт и посадку", { exact: true }).check();
  await page.getByLabel("Плановый набор, м/с", { exact: true }).fill("2");
  await page.getByLabel("Плановое снижение, м/с", { exact: true }).fill("1");
  await page.getByLabel("Расход на наборе / крейсерский", { exact: true }).fill("1.5");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await page.getByLabel("Плановый набор, м/с", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Восстановить", exact: true }).click();
  await expect(page.getByLabel("Плановый набор, м/с", { exact: true })).toHaveValue("2");
  const planned = page.waitForResponse((r) => r.url().endsWith("/v1/plans") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  const response = await planned;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON().drones[0].takeoff_landing_profile.climb_rate_mps).toBe(2);
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  const phasesPending = page.waitForResponse((r) => r.url().endsWith("/v1/exports/geojson"));
  await page.getByRole("button", { name: "GeoJSON", exact: true }).click();
  const phases = await (await phasesPending).json();
  const missions = phases.features.map((feature: { properties: { vertical_climb_duration_minutes: number; vertical_descent_duration_minutes: number } }) => feature.properties);
  expect(missions.length).toBeGreaterThan(0);
  for (const mission of missions) {
    expect(mission.vertical_climb_duration_minutes).toBeCloseTo(100 / 2 / 60);
    expect(mission.vertical_descent_duration_minutes).toBeCloseTo(100 / 60);
  }
  await expect(page.getByText("Взлёт и посадка:", { exact: false })).toBeVisible();
});
