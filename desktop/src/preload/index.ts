// preload 只做一件事：把 main 撮合的 MessagePort 连同握手号转交页面；不 exposeInMainWorld 任何函数（§4.4）。
// 用 on 而不是 once：页面不重载时 main 也会重新撮合（host 重启、TI-2 钩子），once 会让之后的端口全部丢失（S22 🟡-1 ①）。
import { ipcRenderer } from "electron";

ipcRenderer.on("ava-port", (e, handshake: unknown) => window.postMessage({ type: "ava-port", handshake }, "*", e.ports));
ipcRenderer.on("ava-menu", (_e, action: unknown) => window.postMessage({ type: "ava-menu", action }, "*"));
// 同步 02-script 编辑器未保存标记给 main，保证 ⌘Q 退出前同样触发未保存文稿确认（不暴露任何全局 API）
window.addEventListener("message", (e) => {
  if (e.source === window && e.data && e.data.type === "ava-dirty") {
    ipcRenderer.send("ava-dirty", Boolean(e.data.dirty));
  }
});
