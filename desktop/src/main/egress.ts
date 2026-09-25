// 出站网络为零（Spec 8 §2.7）：默认 session 上取消一切 scheme ∉ {ava-media, app 自身资源的 file, devtools} 的请求。
// 未打包构建额外放行 electron-vite dev server（renderer 由它提供）。
import { session } from "electron";

export function isAllowedRequest(url: string, appPath: string, devServer: string | null): boolean {
  if (url.startsWith("ava-media:") || url.startsWith("devtools:")) return true;
  if (url.startsWith("file:")) {
    try {
      const u = new URL(url);
      if (u.host !== "") return false;
      const p = decodeURIComponent(u.pathname);
      return p === appPath || p.startsWith(`${appPath}/`);
    } catch {
      return false;
    }
  }
  if (devServer) {
    const dev = new URL(devServer);
    try {
      const u = new URL(url);
      const sameHost = u.hostname === dev.hostname && u.port === dev.port;
      if (sameHost && (u.protocol === "http:" || u.protocol === "ws:")) return true;
    } catch {
      return false;
    }
  }
  return false;
}

export function installEgressBlock(appPath: string, devServer: string | null): void {
  session.defaultSession.webRequest.onBeforeRequest((details, cb) => {
    cb({ cancel: !isAllowedRequest(details.url, appPath, devServer) });
  });
}
