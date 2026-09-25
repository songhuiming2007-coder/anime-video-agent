// TI-2（伪造端口）、TS-3（路径守卫，含假设 6）、TS-4（Range）、TS-5（出网）、CORS 只放行 renderer 源、假设 3（沙箱剪贴板）。
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { join } from "node:path";
import { expect, test, type Frame } from "@playwright/test";
import { buildFixture, launch, openEpisode, type Fixture, type Launched } from "./fixtures";

let fx: Fixture;
let L: Launched;
let server: Server;
let hits = 0;
let port = 0;

test.beforeAll(async () => {
  fx = buildFixture();
  L = await launch(fx.repo, ["--ava-test-handshake-hook"]);
  server = createServer((_req, res) => {
    hits += 1;
    res.end("egress!");
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  port = (server.address() as { port: number }).port;
});
test.afterAll(async () => {
  await L?.app.close();
  server?.close();
  fx?.cleanup();
});

/** 在 main 里经会话网络栈请求（走 Chromium URL 规范化与 protocol.handle，不受 renderer CSP 影响） */
async function mainFetch(url: string, init: { method?: string; headers?: Record<string, string> } = {}) {
  return L.app.evaluate(async ({ net }, a) => {
    const r = await net.fetch(a.url, { method: a.init.method ?? "GET", headers: a.init.headers });
    const buf = Buffer.from(await r.arrayBuffer());
    return { status: r.status, body: buf.toString("latin1"), headers: Object.fromEntries(r.headers.entries()) };
  }, { url, init });
}

/** 经 UI 的「镜头画廊」入口（shots.list）打开 shots 根下的 html */
async function openGallery(name: string): Promise<Frame> {
  const row = L.page.locator(`[data-testid=gallery][data-name="${name}"]`);
  if ((await row.count()) === 0) await L.page.getByTestId("galleries-toggle").click();
  await row.click();
  await expect(L.page.getByTestId("html-frame")).toHaveAttribute("sandbox", "allow-scripts");
  const el = await L.page.getByTestId("html-frame").elementHandle();
  const frame = await el!.contentFrame();
  await frame!.waitForLoadState();
  return frame!;
}

test("shots.list：镜头画廊入口只列 shots 根顶层 html，按名称自然序", async () => {
  await L.page.getByTestId("galleries-toggle").click();
  await expect(L.page.getByTestId("gallery")).toHaveText(["forge_gallery.html", "T_S01E01_gallery.html"]);
});

test("TS-3 路径守卫：规则 1/2/3 与 URL 规范化（假设 6）", async () => {
  await openEpisode(L.page, "E2E-A");
  const s = async (u: string) => (await mainFetch(u)).status;
  expect(await s("ava-media://episodes/E2E-A/02-script.md")).toBe(200);
  // root 内部的 %2F 注入：只有规则 1 能拦住（拼接结果仍在 root 内，规则 2 会放行）
  expect(await s("ava-media://episodes/E2E-A%2F02-script.md")).toBe(400);
  expect(await s("ava-media://episodes/E2E-A%5C02-script.md")).toBe(400);
  expect(await s("ava-media://shots/..%2F..%2Fbrowser-profile/x.json")).toBe(400);
  expect(await s("ava-media://episodes/E2E-A/%00.md")).toBe(400);
  expect(await s("ava-media://episodes/E2E-A//02-script.md")).toBe(400);
  // 规则 2：root 内指向 root 外（含同一 dataRoot 下另一 root）的符号链接
  expect(await s("ava-media://episodes/E2E-A/link-out.json")).toBe(403);
  expect(await s("ava-media://episodes/E2E-A/link-shots.jpg")).toBe(403);
  // 规则 3
  expect(await s("ava-media://episodes/E2E-A/run.sh")).toBe(415);
  expect(await s("ava-media://episodes/E2E-A/nope.md")).toBe(404);
  expect(await s("ava-media://library/secret.json")).toBe(404);
  // 假设 6：standard scheme 在交给 handler 前已规范化 dot-segment（实测值写入）
  const dot = await mainFetch("ava-media://episodes/E2E-A/../E2E-B/02-script.md");
  expect(dot.status).toBe(200);
  expect(dot.body).toBe(readFileSync(join(fx.dataReal, "episodes/E2E-B/02-script.md"), "latin1")); // 被规范化成 E2E-B/02-script.md，仍在 root 内
  expect(await s("ava-media://episodes/../library/secret.json")).toBe(404); // 规范化后为 episodes/library/secret.json，逃不出 root
  expect(await s("ava-media://episodes/E2E-A/%2e%2e/%2e%2e/library/secret.json")).toBe(404);
  expect(await s("ava-media://shots/%2e%2e/browser-profile/x.json")).toBe(404);
});

test("TS-4 Range：单段 206 且正文等于切片；多段 416；POST 405", async () => {
  const file = readFileSync(join(fx.epA, "05-final.mp4"));
  const r = await mainFetch("ava-media://episodes/E2E-A/05-final.mp4", { headers: { Range: "bytes=100-199" } });
  expect(r.status).toBe(206);
  expect(r.headers["content-range"]).toBe(`bytes 100-199/${file.length}`);
  expect(Buffer.from(r.body, "latin1").equals(file.subarray(100, 200))).toBe(true);
  expect((await mainFetch("ava-media://episodes/E2E-A/05-final.mp4", { headers: { Range: "bytes=0-1,5-6" } })).status).toBe(416);
  expect((await mainFetch("ava-media://episodes/E2E-A/02-script.md", { method: "POST" })).status).toBe(405);
  const full = await mainFetch("ava-media://episodes/E2E-A/05-final.mp4");
  expect(full.status).toBe(200);
  expect(full.headers["accept-ranges"]).toBe("bytes");
});

test("TS-5 出网为零：renderer 的 fetch 与 <img> 被取消；main 会话层 webRequest 同样取消", async () => {
  const target = `http://127.0.0.1:${port}/x`;
  const r = await L.page.evaluate(async (u) => {
    const f = await fetch(u).then(() => "ok", (e: unknown) => String(e));
    const img = await new Promise<string>((res) => {
      const i = new Image();
      i.onload = () => res("load");
      i.onerror = () => res("error");
      i.src = u;
    });
    const ext = await fetch("https://example.com").then(() => "ok", (e: unknown) => String(e));
    return { f, img, ext };
  }, target);
  expect(r.f).toContain("Failed to fetch");
  expect(r.img).toBe("error");
  expect(r.ext).toContain("Failed to fetch");
  // renderer 的 CSP 已先挡一层；下面这条不经 CSP，只有 webRequest 出网拦截能挡住（MUT-29 的证伪点）
  const m = await L.app.evaluate(async ({ session }, u) => session.defaultSession.fetch(u).then((x) => `status=${x.status}`, (e: unknown) => String(e)), target);
  expect(m).toContain("ERR_BLOCKED_BY_CLIENT");
  expect(hits).toBe(0);
});

test("CORS 只放行 renderer 源：renderer fetch 可读正文；响应头按 Origin 精确回显", async () => {
  const text = await L.page.evaluate(() => fetch("ava-media://episodes/E2E-A/02-script.md").then((r) => r.text()));
  expect(text).toContain("# 标题");
  const fromRenderer = await mainFetch("ava-media://episodes/E2E-A/02-script.md", { headers: { Origin: "file://" } });
  expect(fromRenderer.headers["access-control-allow-origin"]).toBe("file://");
  // 外来源不回显（Origin: null 的情形由 corsAllowOrigin 单测兜住：net.fetch 的响应里观察不到 ACAO: null，S21 实测）
  const foreign = await mainFetch("ava-media://episodes/E2E-A/02-script.md", { headers: { Origin: "https://evil.example" } });
  expect(foreign.headers["access-control-allow-origin"]).toBeUndefined();
  // gallery 这类 allow-scripts 的沙箱页（Origin null）读不到期目录文件
  const frame = await openGallery("forge_gallery.html");
  const inFrame = await frame.evaluate(() => fetch("ava-media://episodes/E2E-A/02-script.md").then(() => "ok", (e: unknown) => String(e)));
  expect(inFrame).toContain("Failed to fetch");
});

type HookWin = { __avaTestHandshake: () => number; __avaTestHealth: () => Promise<{ repoRoot: string }> };
const rematch = (X: Launched = L) => X.app.evaluate(() => (globalThis as unknown as { __avaTestRematch: () => number | null }).__avaTestRematch());
const adopted = (X: Launched = L) => X.page.evaluate(() => (window as unknown as HookWin).__avaTestHandshake());
/** 经当前端口发一次 app.health；5 s 无响应判为请求没有到达真实 host */
const healthViaPort = (X: Launched = L) =>
  X.page.evaluate(() =>
    Promise.race([
      (window as unknown as HookWin).__avaTestHealth(),
      new Promise((_r, j) => setTimeout(() => j(new Error("timeout: 请求没有到达真实 host")), 5000)),
    ]),
  ) as Promise<{ repoRoot: string }>;

test("TI-2 重新握手窗口内 iframe 伪造的端口不被采纳；真实端口被采纳，后续请求全部到达真实 host", async () => {
  const frame = await openGallery("forge_gallery.html");
  await expect.poll(() => frame.evaluate(() => (window as unknown as { __forged: number }).__forged)).toBeGreaterThan(3);
  const before = await adopted();
  expect(before).toBeGreaterThan(0);
  expect(before, "伪造端口被采纳（其握手号 ≥ 1e9）").toBeLessThan(1e9);
  const n = await rematch(); // main 延迟 1.5 s 投递真实端口；期间伪造端口每 50 ms 一个
  expect(n).toBeGreaterThan(before); // 撮合确实发生（iframe 加载不会把 rendererLoaded 卡在 false）
  await L.page.waitForTimeout(2500);
  expect(await frame.evaluate(() => (window as unknown as { __forged: number }).__forged)).toBeGreaterThan(30);
  expect(await adopted()).toBe(n); // 真实端口被采纳：不是靠旧端口通过
  expect((await healthViaPort()).repoRoot).toBe(fx.repo);
  expect(await frame.evaluate(() => (window as unknown as { __fakeMsgs: number }).__fakeMsgs)).toBe(0);
});

test("TI-2b 打开 html 预览（iframe）后再重新撮合：新连接可用，旧端口不再使用；被阻止的主框架导航不影响撮合", async () => {
  // 独立实例：被阻止的导航会让 Playwright 认为该页面一直有导航未完成，不能与后续用例共用页面
  const X = await launch(fx.repo, ["--ava-test-handshake-hook"]);
  try {
    await openEpisode(X.page, "E2E-A");
    await X.page.locator(`[data-testid=tree-row][data-rel="04-review.html"]`).click();
    await expect(X.page.getByTestId("html-frame")).toHaveAttribute("sandbox", "");
    const frame = await (await X.page.getByTestId("html-frame").elementHandle())!.contentFrame();
    await frame!.waitForLoadState();
    const before = await adopted(X);
    const n = await rematch(X);
    expect(n).not.toBeNull(); // iframe 加载不会把 rendererLoaded 卡在 false
    expect(n!).toBeGreaterThan(before);
    await expect.poll(() => adopted(X), { timeout: 5000 }).toBe(n);
    // host 绑定新端口时关闭了旧端口：请求若仍走旧端口会超时
    expect((await healthViaPort(X)).repoRoot).toBe(fx.repo);
    // will-navigate 会阻止这次主框架导航；它不能把 rendererLoaded 置 false
    await X.page.evaluate(() => {
      location.href = "https://example.com/";
    });
    await X.page.waitForTimeout(500);
    // 再撮合一次：同一页面不重载也能连续采纳（preload 用 on，不是 once）
    const m = await rematch(X);
    expect(m).not.toBeNull();
    await expect.poll(() => adopted(X), { timeout: 5000 }).toBe(m);
    expect((await healthViaPort(X)).repoRoot).toBe(fx.repo);
  } finally {
    await X.app.close();
  }
});

test("S22 RPC 无超时：host 进程死亡 → 渲染端端口 close，请求以 E_UNREACHABLE 立即拒绝而不是挂起", async () => {
  const X = await launch(fx.repo);
  try {
    expect((await healthViaPort(X)).repoRoot).toBe(fx.repo);
    const hostPid = await X.app.evaluate(({ app }) => app.getAppMetrics().find((m) => m.type === "Utility" && m.name === "ava-host")?.pid ?? null);
    expect(hostPid).not.toBeNull(); // Electron 44 实测：fork 的 serviceName 出现在 metrics 的 name 字段（serviceName 恒为 node.mojom.NodeService）
    process.kill(hostPid!, "SIGKILL");
    const code = () =>
      X.page.evaluate(() =>
        Promise.race([
          (window as unknown as HookWin).__avaTestHealth().then(() => "resolved", (e: { error?: { code?: string } }) => e.error?.code ?? String(e)),
          new Promise((r) => setTimeout(() => r("hang"), 3000)),
        ]),
      );
    await expect.poll(code, { timeout: 10_000 }).toBe("E_UNREACHABLE");
  } finally {
    await X.app.close();
  }
});

test("假设 3：沙箱 iframe（无 allow-same-origin）内 gallery 的「复制锚点」能写入系统剪贴板（经 execCommand 兜底）", async () => {
  const saved = await L.app.evaluate(({ clipboard }) => clipboard.readText());
  const clip = () => L.app.evaluate(({ clipboard }) => clipboard.readText());
  try {
    await L.app.evaluate(({ clipboard }) => clipboard.writeText("before"));
    const frame = await openGallery("T_S01E01_gallery.html");
    await expect.poll(() => frame.evaluate(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))).toBe(true);
    await frame.getByRole("button", { name: "复制锚点" }).click();
    await expect.poll(clip, { timeout: 3000 }).toBe("锚点: T S01E01 00:01.000");
    // 实测机理：不透明源 iframe 未获 clipboard-write 委托，writeText 被 permissions policy 拒绝；
    // 模板的 textarea + execCommand('copy') 兜底在用户手势内生效（点击后 iframe 已聚焦，此时的拒绝原因才是真因）
    const probe = await frame.evaluate(() => navigator.clipboard.writeText("probe").then(() => "resolved", (e: unknown) => String(e)));
    expect(probe).toContain("permissions policy");
  } finally {
    await L.app.evaluate(({ clipboard }, t) => clipboard.writeText(t), saved);
  }
});
