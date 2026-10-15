// D98 三方案的真实原生通知演示（不是截图：横幅、提示音、Dock 弹跳与角标都是系统真的发出来的）。
// 用法（在 desktop/ 下）：npx electron ../docs/dev/plans/2026-10-15-notify-options/demo.cjs A   （A / B / C 各跑一次）
// 跑起来后**立刻切到别的窗口**——真实实现只在 AVA 窗口不在前台时通知。约 25 秒后自动退出。
// 第一次跑 macOS 可能问「Electron 想发送通知」，点允许（这是开发版 Electron，打包版会以 AVA 的名义再问一次）。
const { app, Notification } = require("electron");

const opt = (process.argv.find((a) => /^[ABC]$/.test(a)) || "A").toUpperCase();
const VOL = "卷一 席卷平成的同人雷暴";
// 一条典型会话里的四类时刻（D98 登记的 ①–④），按真实先后排；kind=wait 是「在等人」
const EVENTS = [
  { at: 2, kind: "wait", title: "AVA 等你批准", body: `${VOL} · bash：删除 incoming/ 里 2 个重复下载` },
  { at: 8, kind: "info", title: "AVA 回完了", body: `${VOL} · 12 条 MV 已抓完，3 条分辨率低于 720p，列在回复里` },
  { at: 13, kind: "info", title: "配音跑完了 · 2:05", body: `${VOL} · tts 成功，48 句` },
  { at: 18, kind: "wait", title: "AVA 等你：03.5 顺听", body: `${VOL} · 配音已就绪，等你顺听` },
];

let unseen = 0;
function badge() {
  unseen += 1;
  app.dock?.setBadge(String(unseen));
}
function banner(e, silent) {
  const n = new Notification({ title: e.title, body: e.body, silent });
  n.on("click", () => console.log(`[点了横幅] ${e.title} → 真实实现会把 AVA 窗口提到前台并打开这条会话`));
  n.show();
}
const PLAN = {
  // A：等人才弹横幅（带声音、Dock 弹一下）；回完、作业结束只加角标
  A: (e) => { badge(); if (e.kind === "wait") { banner(e, false); app.dock?.bounce("informational"); } },
  // B：四类都弹横幅；等人的带声音，其余静音
  B: (e) => { badge(); banner(e, e.kind !== "wait"); },
  // C：不弹横幅；等人时 Dock 一直弹到你切回来（critical），其余只加角标
  C: (e) => { badge(); if (e.kind === "wait") app.dock?.bounce("critical"); },
};

app.whenReady().then(() => {
  console.log(`方案 ${opt}：现在切到别的窗口，看右上角与 Dock。`);
  for (const e of EVENTS) setTimeout(() => { console.log(`${e.at}s ${e.kind === "wait" ? "等人" : "通知"}：${e.title}`); PLAN[opt](e); }, e.at * 1000);
  setTimeout(() => { app.dock?.setBadge(""); app.quit(); }, 25_000);
});
app.on("window-all-closed", (e) => e.preventDefault());
