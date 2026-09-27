import { createRoot } from "react-dom/client";
import { App } from "./App";
// 样式顺序固定（Spec 14 §4.1）：token 在前，业务区块在最后，旧规则可被新契约覆盖。
import "./tokens.css";
import "./ui.css";
import "./style.css";
import { applyTheme, readTheme } from "./theme";

// Spec 14 §2.3：createRoot 之前同步应用主题；模块脚本是延迟执行，首帧可能先按系统外观画一帧空白底色（A2，PR3 实测）
applyTheme(readTheme());

createRoot(document.getElementById("root")!).render(<App />);
