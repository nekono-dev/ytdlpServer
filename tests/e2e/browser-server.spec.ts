import { expect, test } from "@playwright/test"

test("browser-server/AC-14: クエリ付きで開くとプロファイルが選択され、未知のサイトでは開始 URL が入る", async ({ page }) => {
  await page.goto("/?profile=niconico")
  await expect(page.locator("#profile")).toHaveValue("niconico")
  await expect(page.locator("#newsite")).toBeHidden()

  await page.goto("/?start_url=" + encodeURIComponent("https://unknown.example/login"))
  await expect(page.locator("#newsite")).toBeVisible()
  await expect(page.locator("#start_url")).toHaveValue("https://unknown.example/login")
})

test("browser-server/AC-14: 保存後はプロファイルの保存状況が未保存から保存済みに変わる", async ({ page, request }) => {
  await page.goto("/?profile=niconico")
  await expect(page.locator("#pinfo")).toContainText("未保存")

  expect((await request.post("/session", { data: { profile: "niconico", mobile: false } })).ok()).toBeTruthy()
  expect((await request.post("/session/commit")).ok()).toBeTruthy()

  await page.goto("/?profile=niconico")
  await expect(page.locator("#pinfo")).toContainText("保存済み")
})
