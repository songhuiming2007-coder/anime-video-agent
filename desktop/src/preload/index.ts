// preload 只做一件事：把 main 撮合的 MessagePort 连同握手号转交页面；不 exposeInMainWorld 任何函数（§4.4）。
// 用 on 而不是 once：页面不重载时 main 也会重新撮合（host 重启、TI-2 钩子），once 会让之后的端口全部丢失（S22 🟡-1 ①）。
import { ipcRenderer } from "electron";

ipcRenderer.on("ava-port", (e, handshake: unknown) => window.postMessage({ type: "ava-port", handshake }, "*", e.ports));
