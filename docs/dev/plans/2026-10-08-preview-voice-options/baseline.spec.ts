// D49 / D50 现状基线：人看到的就是这几张。
import { test } from "../../../../desktop/node_modules/@playwright/test";
import { launch } from "../../../../desktop/e2e/fixtures";
import { cleanup } from "../../../../desktop/tests/helpers";
import { EP025, EP035, fixture, openEp, shoot } from "./shared";

test("现状基线：02.5 编辑器、侧栏收起、03.5 顺听面板", async () => {
  const repo = fixture();
  const L = await launch(repo);
  try {
    await L.page.waitForSelector("[data-testid=episode]");
    await openEp(L.page, EP025);
    await L.page.getByTestId("script-editor").waitFor({ timeout: 20_000 }).catch(() => undefined);
    await shoot(L.page, "base-025-editor");
    await L.page.getByTestId("toggle-left").click();
    await shoot(L.page, "base-025-left-collapsed");
    await L.page.getByTestId("toggle-left").click();
    await openEp(L.page, EP035);
    await L.page.getByTestId("voice-panel").waitFor({ timeout: 20_000 }).catch(() => undefined);
    await shoot(L.page, "base-035-voice");
  } finally {
    await L.app.close().catch(() => undefined);
    cleanup(repo);
  }
});
