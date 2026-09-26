import { createRoot } from "react-dom/client";
import { App } from "./App";
// 样式顺序固定（Spec 14 §4.1）：token 在前，业务区块在最后，旧规则可被新契约覆盖。
import "./tokens.css";
import "./ui.css";
import "./style.css";

createRoot(document.getElementById("root")!).render(<App />);
