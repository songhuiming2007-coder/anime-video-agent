// Spec 14 mock 渲染与审计（Electron 真机）。用法（在 desktop/ 下）：
//   ./node_modules/.bin/electron <本文件> <附件目录> <输出目录>
// 每张 mock 渲染三种主题态：light（系统浅色）、dark（系统深色，走 media 块）、forced-dark（系统浅色 + <html data-theme="dark">，走手动块），
// 各截一张 PNG 并注入 audit.js。只读附件目录，只写输出目录。
const { app, BrowserWindow, nativeTheme } = require("electron");
const fs = require("fs");
const path = require("path");
// 路径按调用时的 cwd 解析（Electron 的 loadFile 否则按本脚本目录解析）；任何异常以非 0 退出，不许挂住
const [A, OUT] = process.argv.slice(-2).map((p) => path.resolve(process.cwd(), p));
process.on("unhandledRejection", (e) => { console.error("FATAL", e); app.exit(2); });
app.whenReady().then(async () => {
  const w = new BrowserWindow({ show: false, width: 1280, height: 820, useContentSize: true, webPreferences: { sandbox: true, contextIsolation: true } });
  const audit = fs.readFileSync(path.join(__dirname, "audit.js"), "utf-8");
  const csp = [];
  w.webContents.on("console-message", (e) => { const m = e.message ?? ""; if (/Content Security Policy|Refused/.test(m)) csp.push(m.slice(0, 160)); });
  let total = 0;
  for (const f of fs.readdirSync(A).filter((x) => x.startsWith("mock-") && x.endsWith(".html")).sort()) {
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
  app.quit();
});
