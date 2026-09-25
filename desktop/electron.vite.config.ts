import { resolve } from "node:path";
import { defineConfig } from "electron-vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";

// 生产 CSP 写在 src/renderer/index.html（§2.7）。dev server 需要 React Refresh 的内联脚本与
// HMR websocket，只在 `electron-vite dev` 下放宽；构建产物永远是 index.html 里的严格版本。
const DEV_CSP =
  "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
  "img-src ava-media: data:; media-src ava-media:; frame-src ava-media:; " +
  "connect-src ava-media: ws://localhost:* http://localhost:*";

function devCsp(): Plugin {
  return {
    name: "ava-dev-csp",
    apply: "serve",
    transformIndexHtml(html) {
      return html.replace(/(<meta http-equiv="Content-Security-Policy" content=")[^"]*(")/, `$1${DEV_CSP}$2`);
    },
  };
}

export default defineConfig({
  main: {
    build: {
      rollupOptions: {
        // host 是 main 构建的第二个 input：utilityProcess.fork(out/main/host.js)
        input: {
          index: resolve(__dirname, "src/main/index.ts"),
          host: resolve(__dirname, "src/host/index.ts"),
        },
      },
    },
  },
  preload: {
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, "src/preload/index.ts") },
      },
    },
  },
  renderer: {
    root: resolve(__dirname, "src/renderer"),
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, "src/renderer/index.html") },
      },
    },
    plugins: [react(), devCsp()],
  },
});
