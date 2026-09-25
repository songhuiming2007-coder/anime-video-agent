// e2e 夹具：临时 repo 副本（data 为指向临时目录的符号链接，便于 TP-6 模拟脱盘）+ 各类产物。
// 媒体用 ffmpeg 现场生成；04-review.html 用 review.py 的真实 CSS 与相对缩略图结构；gallery 用 shots.py 的真实模板。
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { mkdirSync, realpathSync, renameSync, symlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { _electron as electron, type ElectronApplication, type Page } from "@playwright/test";
import { cleanup, makeFixtureRepo, py, tmp } from "../tests/helpers";

export const DESKTOP = resolve(__dirname, "..");
// require("electron") 在 Node 里返回可执行文件路径；Electron 44 起二进制懒下载，缺失时由它补下
export const ELECTRON = createRequire(__filename)("electron") as string;

export const ff = (...args: string[]) => execFileSync("/opt/homebrew/bin/ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args]);

export interface Fixture {
  repo: string;
  dataReal: string;
  epA: string;
  cleanup: () => void;
}

export function buildFixture(opts: { withApprovals?: boolean } = {}): Fixture {
  const repo = makeFixtureRepo(opts);
  // data 换成指向仓库外真实目录的符号链接（与本机 data -> 外置盘同构）
  const dataReal = join(tmp("dataReal"), "anime-video-data");
  renameSync(join(repo, "data"), dataReal);
  symlinkSync(dataReal, join(repo, "data"));
  const eps = join(dataReal, "episodes");
  const epA = join(eps, "E2E-A");
  mkdirSync(join(epA, "03-audio"), { recursive: true });
  mkdirSync(join(epA, "04-thumbs"));
  mkdirSync(join(epA, "07-cover"));
  writeFileSync(join(epA, "01-topic.md"), "# E2E\n类型: 杂谈\n");
  writeFileSync(join(epA, "02-script.md"), "# 标题\n\n<script>window.__pwned = 1</script>\n\n正文 **加粗**，[外链](https://example.com)\n");
  writeFileSync(join(epA, "04-clips.json"), '{"segments":[{"index":1,"mtime_ns":1790171112636927676}],"nested":{"deep":{"deeper":[1,2,3]}}}\n');
  writeFileSync(join(epA, "big.log"), "x".repeat(5 * 1024 * 1024 + 100));
  writeFileSync(join(epA, "run.sh"), "echo hi\n");
  ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=1", "-frames:v", "1", join(epA, "04-thumbs/t1.jpg"));
  ff("-f", "lavfi", "-i", "color=c=red:size=160x90", "-frames:v", "1", join(epA, "04-thumbs/t2.jpg"));
  ff("-f", "lavfi", "-i", "color=c=blue:size=64x64", "-frames:v", "1", join(epA, "07-cover/c1.png"));
  ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001", "-t", "20", "-pix_fmt", "yuv420p", "-c:v", "libx264", join(epA, "05-final.mp4"));
  // TP-6 专用：足够大，Chromium 播放时不会一次读完，响应流（及其 fd）在播放期间保持打开
  ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "60", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "8M", join(epA, "big.mp4"));
  for (const n of ["seg-01", "seg-02", "seg-10"]) {
    ff("-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", join(epA, `03-audio/${n}.wav`));
  }
  py(
    `import sys
from pathlib import Path
from pipeline.review import CSS
ep = Path(sys.argv[1])
body = '<div class="q">查询 <b>测试</b></div><div class="clip"><div class="shots"><img src="04-thumbs/t1.jpg" loading="lazy"><img src="04-thumbs/t2.jpg" loading="lazy"></div></div>'
(ep / "04-review.html").write_text(f"<!doctype html><meta charset=utf-8><title>E2E 抽检</title><style>{CSS}</style>" + body, encoding="utf-8")`,
    [epA],
  );
  // root 外与跨 root 的符号链接（TS-3）
  writeFileSync(join(dataReal, "library/secret.json"), '{"secret":1}\n');
  symlinkSync(join(dataReal, "library/secret.json"), join(epA, "link-out.json"));
  mkdirSync(join(dataReal, "library/shots/frames/T_S01E01"), { recursive: true });
  ff("-f", "lavfi", "-i", "color=c=green:size=160x90", "-frames:v", "1", join(dataReal, "library/shots/frames/T_S01E01/00001.jpg"));
  symlinkSync(join(dataReal, "library/shots/frames/T_S01E01/00001.jpg"), join(epA, "link-shots.jpg"));
  mkdirSync(join(dataReal, "browser-profile"));
  writeFileSync(join(dataReal, "browser-profile/x.json"), '{"cookie":1}\n');
  // shots 根：真实 shots.gallery() 产出的画廊（N29 修复后按钮属性已正确转义，S23 起不再手写卡片）+ TI-2 的伪造端口页
  writeFileSync(
    join(dataReal, "library/shots/T_S01E01.json"),
    JSON.stringify({ meta: { scene_threshold: 8.0, min_shot: 0.5, source: "/x/T_S01E01.mkv" }, shots: [{ i: 0, start: 1.0, end: 3.0, rep: 2.0 }] }),
  );
  py(
    `import sys
from pathlib import Path
from pipeline import shots
out = Path(sys.argv[1])
shots.gallery("T", "S01E01", out_dir=out, dest_dir=out / "frames", check=False)`,
    [join(dataReal, "library/shots")],
  );
  writeFileSync(
    join(dataReal, "library/shots/forge_gallery.html"),
    `<!doctype html><meta charset=utf-8><title>forge</title><script>
window.__fakeMsgs = 0; window.__forged = 0;
function forge() {
  var ch = new MessageChannel();
  ch.port2.onmessage = function () { window.__fakeMsgs++; };
  ch.port2.start();
  // 与 preload 投递的形状完全相同，握手号取极大值：只有 source 校验能挡住它（MUT-13b）
  parent.postMessage({ type: "ava-port", handshake: 1e9 + window.__forged }, "*", [ch.port1]);
  window.__forged++;
}
setInterval(forge, 50);
</script><body>forging</body>`,
  );
  const epB = join(eps, "E2E-B");
  mkdirSync(epB);
  writeFileSync(join(epB, "01-topic.md"), "# B\n");
  writeFileSync(join(epB, "02-script.md"), "# B 稿\n");
  // 不在任何停机点的期（只有选题）：切过去不会触发停机点自动呼出，TP-3 的「切换期卸载媒体」才不被预览替换掩盖（S23 MUT-33）
  const epC = join(eps, "E2E-C");
  mkdirSync(epC);
  writeFileSync(join(epC, "01-topic.md"), "# C\n");
  return { repo, dataReal: realpathSync(dataReal), epA, cleanup: () => { cleanup(repo); cleanup(resolve(dataReal, "..")); } };
}

export interface Launched {
  app: ElectronApplication;
  page: Page;
  stdout: () => string;
  userData: string;
}

export async function launch(repo: string, extraArgs: string[] = [], userData = tmp("ud")): Promise<Launched> {
  const app = await electron.launch({
    executablePath: ELECTRON,
    args: [DESKTOP, `--ava-repo-root=${repo}`, `--ava-user-data=${userData}`, ...extraArgs],
    cwd: DESKTOP,
  });
  let out = "";
  app.process().stdout?.on("data", (b: Buffer) => (out += b.toString()));
  app.process().stderr?.on("data", (b: Buffer) => (out += b.toString()));
  const page = await app.firstWindow();
  await page.waitForSelector("[data-testid=episode]", { timeout: 30_000 }).catch(() => undefined);
  return { app, page, stdout: () => out, userData };
}

export async function openEpisode(page: Page, epKey: string): Promise<void> {
  await page.locator("[data-testid=episode]", { hasText: epKey }).first().click();
  await page.locator("[data-testid=tree-row][data-rel='01-topic.md']").waitFor();
}

export async function pick(page: Page, rel: string): Promise<void> {
  const row = page.locator(`[data-testid=tree-row][data-rel="${rel}"]`);
  if ((await row.count()) === 0 && !rel.includes("/")) await openEpisode(page, "E2E-A"); // 失败重启 worker 后的前置状态
  await row.click();
}
