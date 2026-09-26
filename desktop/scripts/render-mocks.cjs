// Spec 14 §2.7 的 mock 渲染与审计（Electron 真机）。用法（在 desktop/ 下）：
//   ./node_modules/.bin/electron scripts/render-mocks.cjs <附件目录> <输出目录>
// 附件目录里放 mock-*.html、tokens.css、ui.css、style.css、mock.css 与 tools/audit.js（Spec 14 的附件目录）。
// 每张 mock 渲染三种主题态：light（系统浅色）、dark（系统深色，走 media 块）、forced-dark（系统浅色 + <html data-theme="dark">，
// 走手动块），各截一张 PNG 并注入审计；只读附件目录，只写输出目录。PNG 不进 git。
// 与附件 tools/render.cjs 的差别有两处（Spec 14 §8 PR1）：路径全取参数、按 cwd 解析（附件目录归档后不复位）；出错退出码 2，
//   TOTAL_FAIL / CSP_VIOLATIONS 不为 0 时退出码 1（同 tk.mjs 的兜底纪律，红队二轮 B2）。
const { app, BrowserWindow, nativeTheme } = require("electron");
const fs = require("fs");
const path = require("path");

const [A, OUT] = process.argv.slice(-2).map((p) => path.resolve(process.cwd(), p));
if (!A || !OUT) {
  console.error("用法：electron scripts/render-mocks.cjs <附件目录> <输出目录>");
  app.exit(2);
}
process.on("unhandledRejection", (e) => {
  console.error("FATAL", e);
  app.exit(2);
});

app.whenReady().then(async () => {
  const audit = fs.readFileSync(path.join(A, "tools", "audit.js"), "utf-8");
  const mocks = fs.readdirSync(A).filter((x) => x.startsWith("mock-") && x.endsWith(".html")).sort();
  if (mocks.length === 0) {
    console.error(`附件目录里没有 mock-*.html：${A}`);
    app.exit(2);
  }
  fs.mkdirSync(OUT, { recursive: true });
  const w = new BrowserWindow({ show: false, width: 1280, height: 820, useContentSize: true, webPreferences: { sandbox: true, contextIsolation: true } });
  const csp = [];
  w.webContents.on("console-message", (e) => {
    const m = e.message ?? "";
    if (/Content Security Policy|Refused/.test(m)) csp.push(m.slice(0, 160));
  });
  let total = 0;
  for (const f of mocks) {
    for (const mode of ["light", "dark", "forced-dark"]) {
      console.log("render", f, mode);
      nativeTheme.themeSource = mode === "dark" ? "dark" : "light";
      await w.loadFile(path.join(A, f));
      if (mode === "forced-dark") await w.webContents.executeJavaScript(`document.documentElement.dataset.theme = "dark"`);
      await new Promise((r) => setTimeout(r, 250));
      fs.writeFileSync(path.join(OUT, f.replace(".html", `-${mode}.png`)), (await w.webContents.capturePage()).toPNG());
      const r = await w.webContents.executeJavaScript(audit);
      const bad = r.fails.length + r.small.length + r.nonText.length + r.bgImage.length;
      total += bad;
      console.log(`${f} ${mode}: checked=${r.n} contrastFail=${r.fails.length} nonTextFail=${r.nonText.length} bgImageFail=${r.bgImage.length} skippedMedia=${r.skippedMedia} small=${r.small.length}`);
      for (const x of [...r.fails, ...r.nonText, ...r.bgImage, ...r.small]) console.log("   ", JSON.stringify(x));
    }
  }
  console.log(`TOTAL_FAIL=${total} CSP_VIOLATIONS=${csp.length}`, csp.slice(0, 3));
  app.exit(total === 0 && csp.length === 0 ? 0 : 1);
});
