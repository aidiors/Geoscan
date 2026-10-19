import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { flightPackageFiles } from "./flightPackage";

test("downloads separate sortie files and a readable dispatch sheet", async ({ page }, testInfo) => {
  await page.goto("/");
  const field = { type: "FeatureCollection", features: [{ type: "Feature", properties: { name: "Dispatch field" },
    geometry: { type: "Polygon", coordinates: [[[37.394, 55.692], [37.414, 55.692], [37.414, 55.698], [37.394, 55.698], [37.394, 55.692]]] },
  }] };
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name: "dispatch.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(field)) });
  await page.getByRole("button", { name: "Импортировать зону" }).click();
  for (const name of ["Геоскан 201", "Геоскан 801"]) {
    await page.locator(".fleet-row").filter({ hasText: name }).click();
    await page.getByLabel("Использовать в расчёте", { exact: true }).uncheck();
  }
  await page.getByLabel("Макс. маршрут, км", { exact: true }).first().fill("1");
  await page.getByRole("checkbox", { name: /Быстрый расчёт/ }).check();
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled();
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await expect(page.getByText(/Откройте dispatch.html/)).toBeVisible();
  for (const format of ["QGroundControl", "Mission Planner"]) {
    const pending = page.waitForResponse((r) => r.url().endsWith("/v1/exports/flight-package"));
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button", { name: format, exact: true }).click();
    const response = await pending;
    expect(response.status()).toBe(200);
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe("flight-package.zip");
    const downloadedPath = testInfo.outputPath(`package-${format.replaceAll(" ", "-")}.zip`);
    await download.saveAs(downloadedPath);
    const files = flightPackageFiles(await readFile(downloadedPath));
    const manifest = JSON.parse(files.get("manifest.json")!.toString());
    expect(manifest.sorties.length).toBeGreaterThan(1);
    for (const sortie of manifest.sorties) {
      expect(createHash("sha256").update(files.get(sortie.file)!).digest("hex")).toBe(sortie.sha256);
    }
    const sheet = await page.context().newPage();
    await sheet.setContent(files.get("dispatch.html")!.toString());
    await expect(sheet.getByRole("heading", { name: "Порядок выполнения вылетов" })).toBeVisible();
    await expect(sheet.getByRole("row")).toHaveCount(manifest.sorties.length + 1);
    await sheet.screenshot({ path: testInfo.outputPath(`dispatch-${format.replaceAll(" ", "-")}.png`), fullPage: true });
    await sheet.close();
  }
});
