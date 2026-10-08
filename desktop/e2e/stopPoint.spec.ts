// Spec 11 PR3~PR5 / Spec 12 PR3 的端到端（TE-1~TE-4）：02.5 编辑器闭环、冲突拒存、03.5 顺听闭环、人时、
// 封面导入与 09 定稿 ack。夹具一律临时 repo 副本（红队 🔵-6：严禁指向真实 data/）。
import { chmodSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { cleanup, makeFixtureRepo, py } from "../tests/helpers";
import { spawns, openEp, card, readStore, type SpawnRec } from "./ackFixtures";
import { launch, type Launched } from "./fixtures";
import { stubConfirm } from "./sessionFixtures";

const REPO = makeRepoRoots();
interface Ctx {
  repo: string;
  ep: string;
}
const opened: { L?: Launched; repo: string }[] = [];

function makeRepoRoots(): string {
  return makeFixtureRepo();
}
function newCtx(epKey: string): Ctx {
  const ep = join(REPO, "data/episodes", epKey);
  mkdirSync(join(ep, "03-audio"), { recursive: true });
  return { repo: REPO, ep };
}

async function start(ctx: Ctx): Promise<Launched> {
  const L = await launch(ctx.repo);
  opened.push({ L, repo: ctx.repo });
  return L;
}
test.afterEach(async () => {
  for (const o of opened.splice(0)) {
    await o.L?.app.close().catch(() => undefined);
  }
});
test.afterAll(() => {
  cleanup(REPO);
});

const spawnNames = (L: Launched, template: string): SpawnRec[] => spawns(L).filter((s) => s.template === template);

const SCRIPT_V1 = "## 段落 1\n\n配音：雪乃和大老师都没想到。\n\n画面：\n  查询: 甲\n";
const SCRIPT_V2 = "## 段落 1\n\n配音：雪乃和大老师都没想到吧。\n\n画面：\n  查询: 甲\n";
const SCRIPT_TWO = `${SCRIPT_V2}\n## 段落 2\n\n配音：团子说得对。\n\n画面：\n  查询: 乙\n`;

/** 02.5 停机点（无 02-diff.patch → 02.5 人审改稿） */
function epAt025(name: string, script = SCRIPT_V1): Ctx {
  const ctx = newCtx(name);
  writeFileSync(join(ctx.ep, "01-topic.md"), "# 选题\n类型：杂谈\n");
  writeFileSync(join(ctx.ep, "02-script.draft.md"), "# 草稿\n");
  writeFileSync(join(ctx.ep, "02-script.md"), script);
  return ctx;
}

/** 03.5 形态：稿件两段 + manifest + 两段 wav + 一段 attic 快照（回滚按钮可用性的依据） */
function epAt035(name: string, engine = "mlx"): Ctx {
  const ctx = epAt025(name, SCRIPT_TWO);
  writeFileSync(join(ctx.ep, "03-audio/manifest.json"), JSON.stringify({ engine, segments: [{ index: 1, duration: 4 }, { index: 2, duration: 2 }] }));
  for (const n of ["seg-01.wav", "seg-02.wav"]) writeFileSync(join(ctx.ep, `03-audio/${n}`), "RIFF");
  const attic = join(ctx.ep, "03-audio/attic/20260101-000000");
  mkdirSync(attic, { recursive: true });
  writeFileSync(join(attic, "manifest.json"), JSON.stringify({ engine, segments: [{ label: "1", file: "seg-01.wav", duration: 4 }] }));
  writeFileSync(join(attic, "seg-01.wav"), "RIFF");
  return ctx;
}

test("TE-1 02.5 闭环：编辑器装载 → 改稿（dirty 禁封板）→ 保存（SAVE_SCRIPT）→ 封板（02-diff.patch）→ 批准，全程零终端", async () => {
  const ctx = epAt025("SP11-TE1");
  const L = await start(ctx);
  await openEp(L.page, "SP11-TE1");
  // 02.5 pending → 自动呼出 02-script.md → 编辑器
  await L.page.getByTestId("script-editor").waitFor({ timeout: 15_000 });
  await expect(L.page.getByTestId("editor-src")).toContainText("雪乃和大老师都没想到。");
  const c = card(L.page, "02.5");
  await expect(c).toBeVisible();
  const firstId = await c.getAttribute("data-approval-id");

  // 改稿：全选替换（CodeMirror 的 .cm-content 是可编辑的）
  await L.page.getByTestId("editor-src").locator(".cm-content").click();
  await L.page.keyboard.press("Meta+A");
  await L.page.keyboard.type(SCRIPT_V2);
  await expect(L.page.getByTestId("script-editor")).toHaveAttribute("data-dirty", "1");
  await expect(L.page.getByTestId("seal-script")).toBeDisabled(); // 红队 🔴-2 / MUT-16

  // 保存：指纹携带的是打开时的磁盘版本
  const beforeStat = statSync(join(ctx.ep, "02-script.md"), { bigint: true });
  await L.page.getByTestId("save-script").click();
  await expect(L.page.getByTestId("save-msg")).toContainText("已保存");
  await expect(L.page.getByTestId("script-editor")).toHaveAttribute("data-dirty", "0");
  expect(readFileSync(join(ctx.ep, "02-script.md"), "utf-8")).toContain("都没想到吧");
  const saves = spawnNames(L, "SAVE_SCRIPT");
  expect(saves).toHaveLength(1);
  expect(saves[0].argv).toContain(`--expect-size=${beforeStat.size}`);
  expect(saves[0].argv).toContain(`--expect-mtime-ns=${beforeStat.mtimeNs}`);

  // 封板：产出 patch（与终端同一条 git 命令）
  await L.page.getByTestId("seal-script").click();
  await expect(L.page.getByTestId("seal-msg")).toContainText("已封板");
  expect(existsSync(join(ctx.ep, "02-diff.patch"))).toBe(true);
  expect(readFileSync(join(ctx.ep, "02-diff.patch"), "utf-8")).toContain("都没想到吧");
  expect(spawnNames(L, "SEAL_SCRIPT")).toHaveLength(1);

  // 保存导致指纹漂移 → H5 自愈把旧 pending 转 SUPERSEDED、新建 pending；等新对象出现再批准
  await expect.poll(async () => (await c.getAttribute("data-approval-id")) !== firstId, { timeout: 20_000 }).toBe(true);
  await expect(c.getByTestId("approve")).toBeEnabled({ timeout: 20_000 });
  await c.getByTestId("approve").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText(/已批准|已确认批准/);
  const obj = readStore(ctx.ep).find((o) => o.approval_id === firstId);
  expect(obj?.status).toBe("superseded");
  const fresh = readStore(ctx.ep).find((o) => o.type === "02.5" && o.status === "approved");
  expect(fresh?.confirmed_by).toBe("cli");
});

test("TE-2 冲突拒存：编辑器打开后外部改写 → 保存被拒、磁盘字节为外部版、UI 提示可见", async () => {
  const ctx = epAt025("SP11-TE2");
  const L = await start(ctx);
  await openEp(L.page, "SP11-TE2");
  await L.page.getByTestId("script-editor").waitFor({ timeout: 15_000 });
  const external = "## 外部改写\n\n配音：外部版本。\n";
  writeFileSync(join(ctx.ep, "02-script.md"), external);
  await L.page.getByTestId("editor-src").locator(".cm-content").click();
  await L.page.keyboard.press("Meta+A");
  await L.page.keyboard.type("## 我的改动\n");
  await L.page.getByTestId("save-script").click();
  await expect(L.page.getByTestId("save-conflict")).toBeVisible({ timeout: 15_000 });
  await expect(L.page.getByTestId("save-conflict")).toContainText("磁盘版本已变");
  expect(readFileSync(join(ctx.ep, "02-script.md"), "utf-8")).toBe(external); // 一字不写
  // 重新载入需二次确认
  await L.page.getByTestId("reload-script").click();
  await expect(L.page.getByTestId("reload-script")).toContainText("确认丢弃");
  await L.page.getByTestId("reload-script").click();
  await expect(L.page.getByTestId("save-conflict")).toHaveCount(0);
  await expect(L.page.getByTestId("editor-src")).toContainText("外部版本");
});

test("TE-3 03.5 闭环：段落列表（ava-media://）→ 解析 → 确认落盘 → 撤回 → 回滚可用性与 attic 一致 → done 确认框取消不 spawn", async () => {
  const ctx = epAt035("SP11-TE3");
  const L = await start(ctx);
  await openEp(L.page, "SP11-TE3");
  // 点开 03-audio 目录 → 顺听面板替下音频队列
  await L.page.locator("[data-testid=tree-row][data-rel='03-audio']").click();
  const panel = L.page.getByTestId("voice-panel");
  await panel.waitFor({ timeout: 15_000 });
  expect(spawnNames(L, "VOICE_INFO")).toHaveLength(1);
  await expect(panel.getByTestId("seg-row")).toHaveCount(2);
  // 播音走 renderer 原生 <audio> + ava-media://（不打开外部播放器）
  await panel.getByTestId("seg-play").first().click();
  await expect(panel.getByTestId("voice-audio")).toHaveAttribute("src", /^ava-media:\/\/episodes\/.*seg-01\.wav$/);

  // 解析 → 确认卡 → 确认落盘（D50-A S6：文法录入收进「按文法录入」折叠区）
  await panel.getByTestId("voice-grammar").click();
  await panel.getByTestId("voice-raw").fill("1段 雪乃 改成 xuě nǎi");
  await panel.getByTestId("voice-parse").click();
  await expect(panel.getByTestId("voice-card")).toBeVisible();
  await expect(panel.getByTestId("voice-card")).toContainText("雪乃");
  await panel.getByTestId("voice-add").click();
  await expect(panel.getByTestId("voice-msg")).toContainText("已落盘");
  const corrections = JSON.parse(readFileSync(join(ctx.ep, "03-audio/corrections.json"), "utf-8")) as { id: number; applied: boolean }[];
  expect(corrections).toHaveLength(1);
  await expect(panel.getByTestId("pending-row")).toHaveCount(1);

  // 撤回 → 条目消失
  await panel.getByTestId("pending-retract").click();
  await expect(panel.getByTestId("pending-row")).toHaveCount(0);

  // 回滚只看 has_attic（段 1 有快照、段 2 无）
  await expect(panel.getByTestId("seg-revert")).toHaveCount(1);

  // done：无 pending 时禁用；有 pending + 确认框取消 → 不 spawn 长任务
  await expect(panel.getByTestId("voice-done")).toBeDisabled();
  await stubConfirm(L, false);
  await panel.getByTestId("voice-raw").fill("2段 语气发飘 换种子");
  await panel.getByTestId("voice-parse").click();
  await panel.getByTestId("voice-add").click();
  await expect(panel.getByTestId("voice-done")).toBeEnabled();
  await panel.getByTestId("voice-done").click();
  await expect(panel.getByTestId("voice-msg")).toContainText("已取消");
  expect(spawnNames(L, "RUN_TTS_APPLY_PATCH")).toHaveLength(0);
});

test("TE-4 人时：打开 02.5 编辑器 → 停留 → 关闭 → human_time.json 恰增一条 source=desktop；期视图读数可见", async () => {
  const ctx = epAt025("SP11-TE4");
  const L = await start(ctx);
  await openEp(L.page, "SP11-TE4");
  await L.page.getByTestId("script-editor").waitFor({ timeout: 15_000 });
  await L.page.waitForTimeout(1500); // 真实墙钟停留（不设时长下限）
  // 关闭审阅面：点开同期的另一个文件（02-script.md → 01-topic.md）
  await L.page.locator("[data-testid=tree-row][data-rel='01-topic.md']").click();
  await expect(L.page.getByTestId("script-editor")).toHaveCount(0);
  const ht = join(ctx.ep, "human_time.json");
  await expect.poll(() => existsSync(ht), { timeout: 15_000 }).toBe(true);
  await expect.poll(() => JSON.parse(readFileSync(ht, "utf-8")).filter((r: { source?: string }) => r.source === "desktop").length).toBe(1);
  const rec = JSON.parse(readFileSync(ht, "utf-8"))[0] as { stop: string; minutes: number };
  expect(rec.stop).toBe("02.5");
  expect(rec.minutes).toBeGreaterThan(0);
  // 期视图读数
  await openEp(L.page, "SP11-TE4");
  await expect(L.page.getByTestId("human-time")).toContainText("02.5");
});

test("D50-A S6 就地纠错：选中段落文字 → 小卡读音核对 → 记下（期级）→ 待重配条与标黄；同音字锁定全局", async () => {
  const ctx = epAt035("SP11-S6");
  const L = await start(ctx);
  await openEp(L.page, "SP11-S6");
  await L.page.locator("[data-testid=tree-row][data-rel='03-audio']").click();
  const panel = L.page.getByTestId("voice-panel");
  await panel.waitFor({ timeout: 15_000 });
  // 选中段 1 里的「雪乃」（程序化选区 + mouseup：打开小卡不是写操作）
  await panel.locator("[data-testid=seg-row][data-label='1'] [data-testid=seg-text]").evaluate((el) => {
    const node = el.firstChild as Text;
    const at = node.data.indexOf("雪乃");
    const r = document.createRange();
    r.setStart(node, at);
    r.setEnd(node, at + 2);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(r);
    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  const fix = panel.getByTestId("voice-fix");
  await expect(fix).toContainText("「雪乃」应读");
  // 同音字 → 范围锁定全局（期级不加同音字）
  await fix.getByTestId("fix-method-homophone").click();
  await expect(fix.getByTestId("fix-scope-seg")).toBeDisabled();
  await expect(fix.getByTestId("fix-scope-global")).toHaveAttribute("aria-pressed", "true");
  await fix.getByTestId("fix-method-pinyin").click();
  // 音节数不对 → 核对不过、记下禁用；改对 → 通过
  await fix.getByTestId("fix-pinyin").fill("xue3");
  await expect(fix.getByTestId("fix-check")).toContainText("2 个字", { timeout: 15_000 });
  await expect(fix.getByTestId("fix-save")).toBeDisabled();
  await fix.getByTestId("fix-pinyin").fill("xue3 nai3");
  await expect(fix.getByTestId("fix-check")).toContainText("通过", { timeout: 15_000 });
  await fix.getByTestId("fix-save").click();
  await expect(panel.getByTestId("voice-msg")).toContainText("已落盘");
  const corrections = JSON.parse(readFileSync(join(ctx.ep, "03-audio/corrections.json"), "utf-8")) as { word: string; target_tone3: string; scope: string }[];
  expect(corrections.map((c) => [c.word, c.target_tone3, c.scope])).toEqual([["雪乃", "xue3nai3", "segment"]]);
  await expect(panel.locator("[data-testid=seg-row][data-label='1'] mark")).toHaveText("雪乃");
  await expect(panel.getByTestId("voice-done")).toContainText("只重配这 1 段");
  await expect(panel.getByTestId("voice-done")).toBeEnabled();
});

test("D50-A S7 卡内打点：三维 1–5 → manifest.human_review 合并写入（不重合成）→ H5 重钉新卡 → 一次批准通过（N61）", async () => {
  const ctx = epAt035("SP11-S7");
  const L = await start(ctx);
  await openEp(L.page, "SP11-S7");
  const c = card(L.page, "03.5");
  await c.waitFor({ timeout: 15_000 });
  const firstId = await c.getAttribute("data-approval-id");
  await expect(c.getByTestId("review-save")).toBeDisabled(); // 三维没打全不能记
  for (const id of ["review-voice-4", "review-prosody-3", "review-misread-5"]) await c.getByTestId(id).click();
  await c.getByTestId("review-save").click();
  await expect.poll(() => (JSON.parse(readFileSync(join(ctx.ep, "03-audio/manifest.json"), "utf-8")) as { human_review?: Record<string, number> }).human_review, { timeout: 15_000 })
    .toEqual({ voice_stability: 4, prosody: 3, misread: 5 });
  expect(spawnNames(L, "TTS_REVIEW")).toHaveLength(1);
  expect(spawnNames(L, "RUN_TTS_APPLY_PATCH")).toHaveLength(0);
  // 打点改写了 03.5 对象钉住的 manifest：宿主 H5 自愈换上新对象（N61：基线在激活时取，打点再快也不被吞进基线），
  // 人只在新卡上点一次批准；全程不出 E_STALE
  const fresh = L.page.locator(`[data-testid=decision][data-stop="03.5"]:not([data-approval-id="${firstId}"])`);
  await expect(fresh.getByTestId("approve")).toBeEnabled({ timeout: 20_000 });
  expect(spawns(L).filter((s) => s.template === "HEAL" && s.trigger === "H5-artifact-drift")).toHaveLength(1);
  await fresh.getByTestId("approve").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText(/已批准|已确认批准/);
  await expect(L.page.getByText(/E_STALE/)).toHaveCount(0);
  expect(readStore(ctx.ep).find((o) => o.approval_id === firstId)?.status).toBe("superseded");
});

test("TE-5 错误态不计时（红队 ④）：审阅面装载失败时人时计时不得开始", async () => {
  const ctx = epAt035("SP11-TE5");
  rmSync(join(ctx.ep, "02-script.md")); // core /voice-info 的前置缺失 → RPC 失败 → 面板停在错误态
  const L = await start(ctx);
  await openEp(L.page, "SP11-TE5");
  await L.page.locator("[data-testid=tree-row][data-rel='03-audio']").click();
  await expect(L.page.getByText(/找不到 02-script\.md/)).toBeVisible({ timeout: 15_000 });
  await expect(L.page.getByTestId("voice-panel")).toHaveCount(0); // 错误态替下整个面板
  await L.page.waitForTimeout(1200);
  await L.page.locator("[data-testid=tree-row][data-rel='01-topic.md']").click();
  await expect(L.page.getByTestId("voice-panel")).toHaveCount(0);
  await L.page.waitForTimeout(1200);
  expect(existsSync(join(ctx.ep, "human_time.json"))).toBe(false);
});

test("TE-6 错误态不计时（红队 ④·编辑器半边）：正文不可读 → 编辑器停在错误态，关闭后不写 human_time.json", async () => {
  const ctx = epAt025("SP11-TE6");
  chmodSync(join(ctx.ep, "02-script.md"), 0o000); // 02.5 停机点在（文件存在），但正文取不到 → 装载失败
  const L = await start(ctx);
  await openEp(L.page, "SP11-TE6");
  await L.page.getByTestId("script-editor").waitFor({ timeout: 15_000 });
  await expect(L.page.getByTestId("editor-src")).toHaveCount(0); // 错误态替下编辑区
  await L.page.waitForTimeout(1200);
  await L.page.locator("[data-testid=tree-row][data-rel='01-topic.md']").click();
  await expect(L.page.getByTestId("script-editor")).toHaveCount(0);
  await L.page.waitForTimeout(1200);
  expect(existsSync(join(ctx.ep, "human_time.json"))).toBe(false);
});

test("Spec 12 TD-1/TE-1：选择器导入封面 → 09 卡选封面填标题 → 批准并记录定稿（finalize 四键落对象与事件）", async () => {
  const ctx = newCtx("SP12-TE1");
  writeFileSync(join(ctx.ep, "01-topic.md"), "# 选题\n类型：杂谈\n");
  writeFileSync(join(ctx.ep, "02-script.md"), SCRIPT_V2);
  writeFileSync(join(ctx.ep, "03-audio/manifest.json"), JSON.stringify({ engine: "mlx", segments: [{ index: 1, duration: 4 }] }));
  writeFileSync(join(ctx.ep, "04-clips.json"), JSON.stringify({ segments: [{ index: 1, status: "ok", duration: 4 }] }));
  writeFileSync(join(ctx.ep, "04-clips.approved.json"), "{}");
  writeFileSync(join(ctx.ep, "05-final.mp4"), "x".repeat(16));
  writeFileSync(join(ctx.ep, "06-check.log"), "ok\n");
  writeFileSync(join(ctx.ep, "07-titles.md"), "# 标题\n");
  mkdirSync(join(ctx.ep, "07-cover"), { recursive: true });
  // 一张真实 PNG（Pillow 产出）作为「人自备图」，直接放 07-cover/（09 选择器的域）
  const coverPath = join(REPO, `${ctx.ep.slice(-8)}-cover.png`);
  py(`import sys\nfrom PIL import Image\nImage.new("RGB", (1280, 720), (10, 20, 30)).save(sys.argv[1])`, [coverPath]);
  writeFileSync(join(ctx.ep, "07-cover/cover-1.png"), readFileSync(coverPath));

  const L = await start(ctx);
  await openEp(L.page, "SP12-TE1");
  // 导入（选择器路径：Playwright 的 setInputFiles 产生可信 change 事件）
  await L.page.getByTestId("cover-file").setInputFiles(coverPath);
  await expect(L.page.getByTestId("cover-import-msg")).toContainText("已导入", { timeout: 15_000 });
  const imports = spawnNames(L, "IMPORT_COVER");
  expect(imports).toHaveLength(1);
  expect(imports[0].argv.some((a) => a.startsWith("--name="))).toBe(true);
  await expect.poll(() => existsSync(join(ctx.ep, "07-cover/import"))).toBe(true);

  // 09 卡：两输入缺一不可（封面选择器是 .cover-grid/.cover-opt[aria-pressed]，S12-R1）
  const c = card(L.page, "09");
  await expect(c).toBeVisible({ timeout: 20_000 });
  await expect(c.getByTestId("approve")).toBeDisabled();
  await c.getByTestId("cover-opt").first().locator("input[type=radio]").check();
  await expect(c.getByTestId("cover-opt").first().locator("input[type=radio]")).toBeChecked();
  // 缩略图必须真的加载出来（ava-media:// + img-src；坏图不能静默通过）
  const thumb = c.getByTestId("cover-opt").first().locator("img");
  await expect(thumb).toBeVisible();
  expect(await thumb.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await c.getByTestId("title-input").fill("雪乃到底适不适合大老师？");
  await expect(c.getByTestId("approve")).toBeEnabled();
  await c.getByTestId("approve").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText("已批准");
  const nine = readStore(ctx.ep).find((o) => o.type === "09" && o.status === "approved") as unknown as { finalize: { cover: string; title: string; cover_size: number; cover_mtime_ns: string } };
  expect(nine.finalize.cover).toBe("07-cover/cover-1.png");
  expect(nine.finalize.title).toBe("雪乃到底适不适合大老师？");
  expect(nine.finalize.cover_size).toBe(statSync(join(ctx.ep, "07-cover/cover-1.png")).size);
  expect(nine.finalize.cover_mtime_ns).toMatch(/^\d+$/); // 十进制字符串（🔴-1 ①）
  const spawnApprove = spawnNames(L, "APPROVE").at(-1)!;
  const i = spawnApprove.argv.indexOf("--cover");
  expect(spawnApprove.argv.slice(i, i + 4)).toEqual(["--cover", "07-cover/cover-1.png", "--title", "雪乃到底适不适合大老师？"]);
});

test("A1 首日实测：CodeMirror 在 Electron renderer 样式真正生效（CSP 下走 ShadowRoot + adoptedStyleSheets）", async () => {
  const ctx = epAt025("SP11-A1");
  const L = await start(ctx);
  await openEp(L.page, "SP11-A1");
  await L.page.getByTestId("script-editor").waitFor({ timeout: 15_000 });
  const src = L.page.getByTestId("editor-src");
  await expect(src.locator(".cm-editor")).toHaveCount(1);
  await expect(src.locator(".cm-line").first()).toBeVisible();
  // Markdown 高亮经 HighlightStyle 产出带 class 的 span（证明 @codemirror/lang-markdown 装载成功）
  await expect(src.locator(".cm-content [class*='ͼ']").first()).toBeVisible();

  // 红队 ①的回归：光是「类名存在」是空断言（样式被 CSP 拦时照样绿）。以下是计算样式级对拍。
  const st = await L.page.evaluate(() => {
    const host = document.querySelector("[data-testid=editor-src]") as HTMLElement;
    const sr = host.shadowRoot; // 有 shadow 根 = 走 adoptedStyleSheets（本轮 🔴 的修法）；null = 旧的 light-DOM 形态
    const scope: Document | ShadowRoot = sr ?? document;
    const token = (name: string, prop: "color" | "backgroundColor" | "fontFamily") => {
      const d = document.createElement("div");
      d.style[prop] = `var(${name})`;
      document.body.appendChild(d);
      const v = getComputedStyle(d)[prop];
      d.remove();
      return v;
    };
    const q = (s: string) => scope.querySelector(s) as HTMLElement | null;
    const line = (needle: string) => [...scope.querySelectorAll<HTMLElement>(".cm-line")].find((l) => l.textContent?.includes(needle)) ?? null;
    const heading = line("##");
    const plain = line("配音");
    // `##` 标记是 processingInstruction（--syntax-meta），标题正文才是 tags.heading（--syntax-heading）
    const hs = [...(heading?.querySelectorAll<HTMLElement>("[class*='ͼ']") ?? [])].find((s) => s.textContent?.includes("段落")) ?? null;
    return {
      headStyles: document.head.querySelectorAll("style").length,
      sheets: sr ? (sr.adoptedStyleSheets ?? []).length : -1,
      bg: getComputedStyle(q(".cm-editor")!).backgroundColor,
      tokenBg: token("--bg-input", "backgroundColor"),
      fontSize: getComputedStyle(q(".cm-scroller")!).fontSize,
      fontFamily: getComputedStyle(q(".cm-scroller")!).fontFamily,
      tokenFont: token("--font-mono", "fontFamily"),
      headingColor: hs ? getComputedStyle(hs).color : null,
      headingWeight: hs ? getComputedStyle(hs).fontWeight : null,
      tokenHeading: token("--syntax-heading", "color"),
      plainColor: plain ? getComputedStyle(plain).color : null,
    };
  });
  // 样式必须全部来自构造式样式表，且页面里不得有运行时注入的 <style>（那会被 CSP 拦，是本轮 🔴 的根因）
  expect(st.headStyles).toBe(0);
  expect(st.sheets).toBeGreaterThan(0);
  expect(st.bg).toBe(st.tokenBg);
  expect(st.bg).not.toBe("rgba(0, 0, 0, 0)");
  expect(st.fontSize).toBe("12px");
  expect(st.fontFamily).toBe(st.tokenFont);
  expect(st.headingColor).toBe(st.tokenHeading);
  expect(st.headingColor).not.toBe(st.plainColor);
  expect(st.headingWeight).toBe("600"); // --weight-semibold

  // 光标：CSP 拦掉样式时 .cm-cursor 是 `position: static; border-left: 0`（聚焦后看不见光标）
  await src.locator(".cm-content").click();
  const cursor = await L.page.evaluate(() => {
    const sr = (document.querySelector("[data-testid=editor-src]") as HTMLElement).shadowRoot;
    const c = (sr ?? document).querySelector<HTMLElement>(".cm-cursor");
    if (!c) return null;
    const cs = getComputedStyle(c);
    return { position: cs.position, width: cs.borderLeftWidth, color: cs.borderLeftColor };
  });
  expect(cursor).not.toBeNull();
  expect(cursor!.position).toBe("absolute");
  expect(cursor!.width).not.toBe("0px");
  expect(cursor!.color).not.toBe("rgba(0, 0, 0, 0)");

  // 键盘路径跨 shadow 边界：焦点在 shadow 内的 .cm-content，Cmd+S 必须冒泡到 .editor-src 的 onKeyDown
  await L.page.keyboard.press("Meta+S");
  await expect(L.page.getByTestId("save-msg")).toContainText("已保存");
  expect(spawnNames(L, "SAVE_SCRIPT")).toHaveLength(1);
});
