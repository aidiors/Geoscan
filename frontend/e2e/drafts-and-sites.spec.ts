import { expect, test } from "@playwright/test";

test("confirms draft actions and returns to the restored assignment", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Открыть черновик", exact: true }).click();
  await expect(page.locator(".draft-feedback.error")).toContainText("не найден");
  await page.getByRole("button", { name: "Сохранить черновик", exact: true }).click();
  await expect(page.locator(".draft-feedback.success")).toContainText("Черновик сохранён");
  await expect(page.locator(".draft-feedback")).toBeFocused();
  const altitude = page.getByLabel("Высота задания MSL, м", { exact: true });
  const original = await altitude.inputValue();
  await altitude.fill("180");
  await page.getByRole("button", { name: "Открыть черновик", exact: true }).click();
  await expect(page.locator("#mission-inputs")).toBeFocused();
  await expect(page.locator(".draft-toast")).toContainText("Черновик открыт");
  await expect(altitude).toHaveValue(original);
  await expect.poll(() => page.locator("#mission-inputs").evaluate((element) => element.scrollTop)).toBe(0);
  await page.getByRole("button", { name: "Закрыть уведомление о черновике" }).click();
  await expect(page.locator(".draft-toast")).toHaveCount(0);
});

test("adds takeoff, landing and alternate sites from the map editor and cancels in view mode", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Добавить площадку", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Редактирование", exact: true }).click();
  const map = page.getByRole("img", { name: "Интерактивная карта зоны обследования" });
  const count = await page.locator(".site-item").count();
  for (const [index, role] of ["takeoff", "landing", "alternate"].entries()) {
    await page.getByRole("button", { name: "Добавить площадку", exact: true }).click();
    await page.getByLabel("Роль новой площадки").selectOption(role);
    const bounds = (await map.boundingBox())!;
    await map.click({ position: { x: bounds.width * .2, y: bounds.height * .4 } });
    await expect(page.locator(".site-item")).toHaveCount(count + index + 1);
    await expect(page.getByLabel("Роль новой площадки")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Добавить площадку", exact: true }).click();
  await page.getByRole("button", { name: "Просмотр", exact: true }).click();
  await expect(page.getByLabel("Роль новой площадки")).toHaveCount(0);
  const bounds = (await map.boundingBox())!;
  await map.click({ position: { x: bounds.width * .2, y: bounds.height * .4 } });
  await expect(page.locator(".site-item")).toHaveCount(count + 3);
});

test("keeps edit tools visible and retains an unfinished contour across tool switches", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Редактирование", exact: true }).click();
  const area = page.getByRole("button", { name: "Нарисовать область", exact: true });
  const site = page.getByRole("button", { name: "Добавить площадку", exact: true });
  await area.click();
  await expect(area).toHaveAttribute("aria-pressed", "true");
  await expect(site).toBeVisible();
  const map = page.getByRole("img", { name: "Интерактивная карта зоны обследования" });
  const bounds = (await map.boundingBox())!;
  await map.click({ position: { x: bounds.width * .2, y: bounds.height * .4 } });
  await page.getByLabel("Название контура").fill("Незавершённый контур");
  await site.click();
  await expect(area).toBeVisible();
  await expect(area).toHaveAttribute("aria-pressed", "false");
  await expect(site).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Роль новой площадки").selectOption("alternate");
  await area.click();
  await expect(site).toHaveAttribute("aria-pressed", "false");
  await expect(area).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Роль новой площадки")).toHaveCount(0);
  await expect(page.getByLabel("Название контура")).toHaveValue("Незавершённый контур");
  await expect(map.locator(".drawing-layer circle")).toHaveCount(1);
  await area.click();
  await expect(map.locator(".drawing-layer circle")).toHaveCount(1);
  await site.click();
  await expect(page.getByLabel("Роль новой площадки")).toHaveValue("alternate");
});
