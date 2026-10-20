import { expect, test } from "@playwright/test";

test("checks fixed-wing phases on transits and keeps unverified autopilot exports unavailable", async ({ page }, testInfo) => {
  await page.goto("/");
  const field = { type:"FeatureCollection", features:[{type:"Feature",properties:{name:"Forward flight field"},
    geometry:{type:"Polygon",coordinates:[[[37.43,55.7],[37.45,55.7],[37.45,55.708],[37.43,55.708],[37.43,55.7]]]}}] };
  await page.locator('input[accept=".geojson,.json,.kml"]').setInputFiles({ name:"forward.geojson",mimeType:"application/geo+json",buffer:Buffer.from(JSON.stringify(field)) });
  await page.getByRole("button", { name:"Импортировать зону" }).click();
  await page.getByRole("button", { name:"Удалить Резервная площадка", exact:true }).click();
  await page.getByRole("button", { name: /Управлять парком/ }).click();
  for (const name of ["Геоскан 801", "Геоскан Gemini"]) {
    await page.locator(".fleet-row").filter({hasText:name}).click();
    await page.getByLabel("Использовать в расчёте", {exact:true}).uncheck();
  }
  await page.locator(".fleet-row").filter({hasText:"Геоскан 201"}).click();
  await page.getByLabel("Радиус разворота этого БВС, м", {exact:true}).fill("50");
  await page.getByText("Лётные ограничения и фазы полёта", { exact: true }).click();
  await page.getByLabel("Учитывать самолётный набор и снижение на ходу", {exact:true}).check();
  await page.getByLabel("Плановый предел набора на ходу, м/с", {exact:true}).fill("5");
  await page.getByLabel("Плановый предел снижения на ходу, м/с", {exact:true}).fill("5");
  await page.getByRole("button", { name: "Готово", exact: true }).click();
  await page.getByLabel("Высота задания MSL, м", {exact:true}).fill("250");
  await page.getByLabel("Отметка поверхности MSL, м", {exact:true}).fill("150");
  await page.getByRole("checkbox", {name:/Быстрый расчёт/}).check();
  await page.getByRole("button", {name:"Сохранить черновик",exact:true}).click();
  await page.getByRole("button", { name: /Управлять парком/ }).click();
  await page.getByLabel("Плановый предел набора на ходу, м/с", {exact:true}).fill("1");
  await page.getByRole("button", { name: "Готово", exact: true }).click();
  await page.getByRole("button", {name:"Открыть черновик",exact:true}).click();
  await page.getByRole("button", { name: /Управлять парком/ }).click();
  await expect(page.getByLabel("Плановый предел набора на ходу, м/с", {exact:true})).toHaveValue("5");
  await page.getByRole("button", { name: "Готово", exact: true }).click();
  await page.getByRole("button", {name:"Рассчитать план",exact:true}).click();
  await expect(page.getByRole("button", {name:"Рассчитать план",exact:true})).toBeEnabled({timeout:120_000});
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
  await expect(page.getByText("Самолётный профиль:", {exact:false})).toBeVisible();
  const profilePlot = page.getByRole("img", {name:"Самолётный профиль высоты по времени"});
  await expect(profilePlot).toBeVisible();
  await profilePlot.scrollIntoViewIfNeeded();
  await profilePlot.screenshot({path:testInfo.outputPath("fixed-wing-profile.png")});
  await expect(page.getByRole("button", {name:"QGroundControl",exact:true})).toBeDisabled();
  await expect(page.getByRole("button", {name:"Mission Planner",exact:true})).toBeDisabled();
  const exported = page.waitForResponse((r) => r.url().endsWith("/v1/exports/geojson"));
  await page.getByRole("button", {name:"GeoJSON",exact:true}).click();
  const geo = await (await exported).json();
  expect(geo.features.length).toBeGreaterThan(0);
  for (const feature of geo.features) {
    expect(feature.geometry.coordinates[0][2]).toBe(150);
    expect(feature.geometry.coordinates.at(-1)[2]).toBe(150);
    expect(feature.properties.forward_flight_phases.maximum_climb_rate_mps).toBeLessThanOrEqual(5);
  }
});
