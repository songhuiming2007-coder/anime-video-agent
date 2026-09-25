// TP-1~TP-6、TI-1：PreviewPane 与只读媒体协议（未打包构建，夹具期）。
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { buildFixture, ff, launch, openEpisode, pick, type Fixture, type Launched } from "./fixtures";
import { cleanup, makeFixtureRepo, tmp, treeManifest } from "../tests/helpers";

let fx: Fixture;
let L: Launched;

test.beforeAll(async () => {
  fx = buildFixture();
  L = await launch(fx.repo);
});
test.afterAll(async () => {
  await L?.app.close();
  fx?.cleanup();
});

test("启动：host 就绪且无损自检通过；DEV BUILD 水印", async () => {
  await expect.poll(() => L.stdout()).toContain("AVA_BOOT host-ready lossless-json=ok");
  await expect(L.page.getByTestId("dev-build")).toBeVisible();
});

test("TI-1 renderer 零 Node；window 上除测试钩子外无任何额外全局（与无 preload 的空白窗口做差集）", async () => {
  const r = await L.page.evaluate(() => ({
    require: typeof (globalThis as { require?: unknown }).require,
    process: typeof (globalThis as { process?: unknown }).process,
    names: Object.getOwnPropertyNames(window),
  }));
  expect(r.require).toBe("undefined");
  expect(r.process).toBe("undefined");
  // 基线：同一个 index.html、同样的 webPreferences，只是不挂 preload（拿不到端口，测试钩子也不会安装）
  const baseline = await L.app.evaluate(async ({ app, BrowserWindow }) => {
    const w = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await w.loadFile(`${app.getAppPath()}/out/renderer/index.html`);
    const names = (await w.webContents.executeJavaScript("Object.getOwnPropertyNames(window)")) as string[];
    w.destroy();
    return names;
  });
  const extra = r.names.filter((k) => !baseline.includes(k)).sort();
  expect(extra).toEqual(["__avaTestCall", "__avaTestHandshake", "__avaTestHealth"]);
});

test("TP-1 04-review.html 在 iframe 内渲染，相对路径缩略图全部加载；iframe 为不透明源", async () => {
  await openEpisode(L.page, "E2E-A");
  await pick(L.page, "04-review.html");
  const frameEl = L.page.getByTestId("html-frame");
  await expect(frameEl).toHaveAttribute("sandbox", "");
  const frame = await (await frameEl.elementHandle())!.contentFrame();
  await expect.poll(async () => frame!.evaluate(() => [...document.images].map((i) => i.complete && i.naturalWidth > 0)), { timeout: 10_000 }).toEqual([true, true]);
  // TI-2 前半：父页面拿不到 contentDocument
  expect(await frameEl.evaluate((f: HTMLIFrameElement) => f.contentDocument === null)).toBe(true);
});

test("TP-2 视频浮层显示 rVFC mediaTime（23.976 fps 夹具拖到 12.5 s）", async () => {
  await pick(L.page, "05-final.mp4");
  const r = await L.page.evaluate(async () => {
    const v = document.querySelector("video") as HTMLVideoElement & { requestVideoFrameCallback: (cb: (n: number, m: { mediaTime: number }) => void) => number };
    await new Promise<void>((res) => (v.readyState >= 1 ? res() : v.addEventListener("loadedmetadata", () => res(), { once: true })));
    const got = new Promise<number>((res) => v.requestVideoFrameCallback((_n, m) => res(m.mediaTime)));
    v.currentTime = 12.5;
    const mediaTime = await got;
    await new Promise((res) => setTimeout(res, 300));
    return { mediaTime, overlay: document.querySelector("[data-testid=timecode]")!.textContent };
  });
  // 期望值实跑后写入：12.5 s 落在第 299 帧（pts = 299 × 1001 / 24000 = 12.4707917 s），不是 12.5xx
  expect(r.mediaTime).toBeCloseTo(12.4707917, 6);
  expect(r.overlay).toBe("00:00:12.470");
});

test("TP-3 点 03-audio/ → 队列为自然序，首段 ended 后自动接第二段；切换期后媒体元素数为 0", async () => {
  await pick(L.page, "03-audio");
  await expect(L.page.getByTestId("audio-queue").locator("li")).toHaveText(["seg-01.wav", "seg-02.wav", "seg-10.wav"]);
  // 挂事件记录实际播放序列（每段只有 0.3 s，轮询采样会错过窗口）
  await L.page.evaluate(() => {
    const a = document.querySelector("audio") as HTMLAudioElement;
    const w = window as unknown as { __played: string[] };
    w.__played = [];
    a.addEventListener("playing", () => w.__played.push(decodeURIComponent(a.src).split("/").pop()!));
    void a.play();
  });
  await expect.poll(() => L.page.evaluate(() => (window as unknown as { __played: string[] }).__played), { timeout: 5000 }).toEqual(["seg-01.wav", "seg-02.wav", "seg-10.wav"]);
  // 切到不在停机点的期：E2E-B 处于 02.5，H1 自愈建出 pending 后自动呼出会替换预览，掩盖「切换期卸载媒体」本身（S23 MUT-33）
  await L.page.locator("[data-testid=episode]", { hasText: "E2E-C" }).click();
  await L.page.getByTestId("center").and(L.page.locator("[data-ep='E2E-C'][data-loading='0']")).waitFor();
  await expect.poll(() => L.page.evaluate(() => document.querySelectorAll("video, audio").length)).toBe(0);
  await openEpisode(L.page, "E2E-A");
});

test("TP-4 .md 中的 <script> 按文本显示不执行；链接不导航；.json 折叠/展开且大数按原文；超 5 MiB 标注截断", async () => {
  await pick(L.page, "02-script.md");
  const mdBox = L.page.getByTestId("markdown");
  await expect(mdBox).toContainText("<script>window.__pwned = 1</script>");
  expect(await L.page.evaluate(() => (window as { __pwned?: number }).__pwned)).toBeUndefined();
  const url = L.page.url();
  await mdBox.getByRole("link", { name: "外链" }).click();
  expect(L.page.url()).toBe(url);

  await pick(L.page, "04-clips.json");
  const json = L.page.getByTestId("json");
  // 默认展开两层：toggle 依次为 根、segments、segments[0]（折叠）、nested、deep（折叠）
  await expect(json).not.toContainText("mtime_ns");
  await json.getByTestId("json-toggle").nth(2).click();
  await expect(json).toContainText('"mtime_ns": 1790171112636927676'); // 大数按源码原文，不是 …927700
  await expect(json).not.toContainText("deeper");
  await json.getByTestId("json-toggle").nth(4).click();
  await expect(json).toContainText("deeper");
  await json.getByTestId("json-toggle").first().click(); // 折叠根
  await expect(json).not.toContainText("segments");

  await pick(L.page, "big.log");
  await expect(L.page.getByTestId("truncated")).toBeVisible();
  const len = await L.page.getByTestId("plain").evaluate((e) => e.textContent!.length);
  expect(len).toBe(5 * 1024 * 1024);
});

test("图片预览与只显示元数据的类型", async () => {
  await openEpisode(L.page, "E2E-A");
  await pick(L.page, "07-cover");
  await pick(L.page, "07-cover/c1.png");
  await expect.poll(() => L.page.getByTestId("image").evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth)).toBe(64);
  await pick(L.page, "run.sh");
  await expect(L.page.locator(".meta")).toContainText("只显示元数据");
});

test("TP-6 播放中普通推出数据盘（真实磁盘映像 + 不带 force 的 diskutil unmount，与 Finder 推出同一条 DiskArbitration 路径）→ 推出成功、横幅 volume-unmounted、app 存活；重新挂载后自动恢复", async () => {
  // S23 门禁 6 修订（经用户同意）：旧版以「改符号链接指向」模拟脱盘，只验证了 reach 变化之后 fd 会释放，
  // 没验证「播放中能否正常推出」——真实推出须在卸载前就不持有 fd（判据 1：测真实产物）。
  test.setTimeout(150_000);
  const dir = tmp("tp6");
  const dmg = join(dir, "tp6.dmg");
  execFileSync("/usr/bin/hdiutil", ["create", "-quiet", "-size", "200m", "-fs", "APFS", "-volname", `AVA-TP6-${process.pid}`, dmg]);
  const attach = () => {
    const out = execFileSync("/usr/bin/hdiutil", ["attach", "-nobrowse", dmg], { encoding: "utf-8" });
    const mnt = /\t(\/Volumes\/[^\t\n]+)\s*$/m.exec(out)![1].trim();
    writeFileSync(join(mnt, ".metadata_never_index"), ""); // 不让 Spotlight 占着卷（否则推出失败的原因不是我们）
    return mnt;
  };
  const mnt = attach();
  const repo = makeFixtureRepo();
  let X: Launched | null = null;
  try {
    const dataReal = join(mnt, "anime-video-data");
    const ep = join(dataReal, "episodes/TP6");
    mkdirSync(ep, { recursive: true });
    mkdirSync(join(dataReal, "library/shots"), { recursive: true });
    writeFileSync(join(ep, "01-topic.md"), "# TP6\n");
    // 足够大（约 60 MB）：Chromium 播放时不会一次读完，旧实现的开放尾端流会在整个播放期间持有 fd
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "60", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "8M", join(ep, "big.mp4"));
    rmSync(join(repo, "data"), { recursive: true });
    symlinkSync(dataReal, join(repo, "data"));
    X = await launch(repo);
    await openEpisode(X.page, "TP6");
    await X.page.locator(`[data-testid=tree-row][data-rel="big.mp4"]`).click();
    await X.page.evaluate(() => (document.querySelector("video") as HTMLVideoElement).play());
    await expect.poll(() => X!.page.evaluate(() => (document.querySelector("video") as HTMLVideoElement).currentTime), { timeout: 15_000 }).toBeGreaterThan(3);
    const r = spawnSync("/usr/sbin/diskutil", ["unmount", mnt], { encoding: "utf-8" });
    const holders = r.status === 0 ? "" : spawnSync("/usr/sbin/lsof", ["+D", mnt], { encoding: "utf-8" }).stdout;
    expect(r.status, `普通推出失败：${r.stdout}${r.stderr}\n${holders}`).toBe(0);
    await expect(X.page.getByTestId("reach-banner")).toContainText("volume-unmounted", { timeout: 5000 });
    expect(X.app.process().exitCode).toBeNull();
    // 重新挂载（同卷名 → 同挂载点）：5 s 内自动恢复
    expect(attach()).toBe(mnt);
    await expect(X.page.getByTestId("reach-banner")).toHaveCount(0, { timeout: 5000 });
  } finally {
    await X?.app.close();
    spawnSync("/usr/bin/hdiutil", ["detach", "-force", mnt]);
    cleanup(dir);
    cleanup(repo);
  }
});

test("TP-5 悬空 data → volume-unmounted；chmod 000 → permission-denied；均无新建目录", async () => {
  const repo = makeFixtureRepo();
  try {
    execFileSync("/bin/rm", ["-r", join(repo, "data")]);
    symlinkSync("/Volumes/NoSuchDisk/x", join(repo, "data"));
    const a = await launch(repo);
    await expect(a.page.getByTestId("reach-banner")).toContainText("volume-unmounted");
    await a.app.close();
    unlinkSync(join(repo, "data"));
    mkdirSync(join(repo, "data/library"), { recursive: true });
    chmodSync(join(repo, "data"), 0o000);
    const b = await launch(repo);
    await expect(b.page.getByTestId("reach-banner")).toContainText("permission-denied");
    await b.app.close();
    chmodSync(join(repo, "data"), 0o755);
    expect(execFileSync("/usr/bin/find", [join(repo, "data")], { encoding: "utf-8" }).trim().split("\n").sort()).toEqual([join(repo, "data"), join(repo, "data/library")]);
  } finally {
    try {
      chmodSync(join(repo, "data"), 0o755);
    } catch {
      /* 已恢复 */
    }
    cleanup(repo);
  }
});

test("TI-3a（预览部分）关闭 heal 后，对 chmod -R a-w 的夹具树执行期列表、树浏览与全部预览 → 功能正常、树清单不变（I1）", async () => {
  const ro = buildFixture({ withApprovals: false }); // 能力探针缺席 → heal 不 spawn
  execFileSync("/bin/chmod", ["-R", "a-w", ro.dataReal]);
  const before = treeManifest(ro.dataReal);
  const R = await launch(ro.repo);
  try {
    const health = (await R.page.evaluate(() => (window as unknown as { __avaTestHealth: () => Promise<unknown> }).__avaTestHealth())) as { capabilities: { approvals: boolean } };
    expect(health.capabilities.approvals).toBe(false);
    await expect(R.page.locator("[data-testid=episode]")).toHaveCount(3); // E2E-A、E2E-B、E2E-C
    await openEpisode(R.page, "E2E-A");
    // 网页（episodes 根，sandbox=""）
    await R.page.locator(`[data-testid=tree-row][data-rel="04-review.html"]`).click();
    const review = await (await R.page.getByTestId("html-frame").elementHandle())!.contentFrame();
    await expect.poll(() => review!.evaluate(() => [...document.images].map((i) => i.complete && i.naturalWidth > 0)), { timeout: 10_000 }).toEqual([true, true]);
    // 视频
    await R.page.locator(`[data-testid=tree-row][data-rel="05-final.mp4"]`).click();
    await expect.poll(() => R.page.evaluate(() => (document.querySelector("video") as HTMLVideoElement | null)?.readyState ?? 0)).toBeGreaterThanOrEqual(1);
    // 音频队列（树浏览子目录）
    await R.page.locator(`[data-testid=tree-row][data-rel="03-audio"]`).click();
    await expect(R.page.getByTestId("audio-queue").locator("li")).toHaveText(["seg-01.wav", "seg-02.wav", "seg-10.wav"]);
    await expect.poll(() => R.page.evaluate(() => (document.querySelector("audio") as HTMLAudioElement | null)?.readyState ?? 0)).toBeGreaterThanOrEqual(1);
    // 图片（树浏览子目录）
    await R.page.locator(`[data-testid=tree-row][data-rel="07-cover"]`).click();
    await R.page.locator(`[data-testid=tree-row][data-rel="07-cover/c1.png"]`).click();
    await expect.poll(() => R.page.getByTestId("image").evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth)).toBe(64);
    // 文本三类 + 只显示元数据
    await R.page.locator(`[data-testid=tree-row][data-rel="02-script.md"]`).click();
    await expect(R.page.getByTestId("markdown")).toContainText("标题");
    await R.page.locator(`[data-testid=tree-row][data-rel="04-clips.json"]`).click();
    await expect(R.page.getByTestId("json")).toContainText("segments");
    await R.page.locator(`[data-testid=tree-row][data-rel="big.log"]`).click();
    await expect(R.page.getByTestId("truncated")).toBeVisible();
    await R.page.locator(`[data-testid=tree-row][data-rel="run.sh"]`).click();
    await expect(R.page.locator(".meta")).toContainText("只显示元数据");
    // shots 根的 gallery（allow-scripts）
    await R.page.getByTestId("galleries-toggle").click();
    await R.page.locator(`[data-testid=gallery][data-name="T_S01E01_gallery.html"]`).click();
    await expect(R.page.getByTestId("html-frame")).toHaveAttribute("sandbox", "allow-scripts");
    const gallery = await (await R.page.getByTestId("html-frame").elementHandle())!.contentFrame();
    await expect.poll(() => gallery!.evaluate(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))).toBe(true);
    // 切到 B 期（订阅/激活第二个期）
    await R.page.locator("[data-testid=episode]", { hasText: "E2E-B" }).click();
    await R.page.locator("[data-testid=tree-row][data-rel='02-script.md']").waitFor();
  } finally {
    await R.app.close();
    const after = treeManifest(ro.dataReal);
    ro.cleanup();
    expect(after).toEqual(before);
  }
});
