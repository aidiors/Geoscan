import { expect, test } from "@playwright/test";

test("navigation selects and focuses the requested section without clipping workspace actions", async ({ page }) => {
  await page.goto("/");
  const workflow = page.getByRole("navigation", { name: "Этапы подготовки задания" });
  for (const [name, target] of [["3 Качество", "mission-quality"], ["4 Экспорт", "mission-exports"], ["2 Расчёт", "mission-results"], ["1 Задание", "mission-inputs"]]) {
    await workflow.getByRole("link", { name, exact: true }).click();
    await expect(workflow.getByRole("link", { name, exact: true })).toHaveAttribute("aria-current", "step");
    await expect(page.locator(`#${target}`)).toBeFocused();
  }
  const restore = page.getByRole("button", { name: "Открыть черновик", exact: true });
  await restore.scrollIntoViewIfNeeded();
  expect(await restore.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await workflow.getByRole("link", { name: "1 Задание", exact: true }).click();
  expect(await page.locator(".controls-panel").evaluate((element) => element.scrollTop)).toBe(0);
});

test("guides an operator from a calculated plan through quality to export and invalidates edited inputs", async ({ page }) => {
  await page.goto("/");
  const workflow = page.getByRole("navigation", { name: "Этапы подготовки задания" });
  await expect(workflow.getByRole("link", { name: "1 Задание" })).toHaveAttribute("aria-current", "step");
  await page.getByRole("button", { name: "Демо-кейсы", exact: true }).click();
  await page.getByRole("button", { name: /Один вылет - несколько участков/ }).click();
  await page.getByRole("button", { name: "Загрузить и рассчитать", exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled({ timeout: 120_000 });
  await expect(workflow.getByRole("button", { name: "Проверить качество", exact: true })).toBeEnabled();
  await workflow.getByRole("button", { name: "Проверить качество", exact: true }).click();
  await expect(workflow.getByRole("link", { name: "К экспорту →", exact: true })).toBeVisible();
  await workflow.getByRole("link", { name: "К экспорту →", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "QGroundControl", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.(plan|zip)$/);
  await page.getByLabel("Максимальный GSD, см", { exact: true }).fill("4");
  await expect(workflow.getByRole("link", { name: "1 Задание" })).toHaveAttribute("aria-current", "step");
  await expect(page.getByRole("button", { name: "QGroundControl", exact: true })).toBeDisabled();
});

test("explains a weather refusal and clears it after the operator fixes the conditions", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Демо-кейсы", exact: true }).click();
  await page.getByRole("button", { name: /Один вылет - несколько участков/ }).click();
  await page.getByRole("button", { name: "Только загрузить", exact: true }).click();
  await page.getByLabel("Скорость, м/с", { exact: true }).fill("30");
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  const issue = page.getByRole("alert").filter({ hasText: "План пока не построен" });
  await expect(issue).toContainText("ветр");
  await expect(page.getByRole("button", { name: "QGroundControl", exact: true })).toBeDisabled();
  await page.getByLabel("Скорость, м/с", { exact: true }).fill("0");
  await expect(issue).not.toBeVisible();
  await page.getByRole("button", { name: "Рассчитать план", exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать план", exact: true })).toBeEnabled();
  await expect(page.getByText("Все применимые проверки пройдены")).toBeVisible();
});
