// 静态守卫的扫描器（TG 系列共用）。检查器都接受 (相对路径, 源码文本)，便于用合成片段自测正反例。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import ts from "typescript";

export const DESKTOP = resolve(__dirname, "../..");
export const SRC = join(DESKTOP, "src");

export interface SourceFile {
  rel: string; // 相对 src/ 的 posix 路径，如 host/spawner.ts
  text: string;
}

export function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

export function sourceFiles(): SourceFile[] {
  return walk(SRC)
    .filter((p) => /\.(ts|tsx)$/.test(p))
    .map((p) => ({ rel: relative(SRC, p).split("\\").join("/"), text: readFileSync(p, "utf-8") }));
}

export function parse(f: SourceFile): ts.SourceFile {
  return ts.createSourceFile(f.rel, f.text, ts.ScriptTarget.Latest, true, f.rel.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
}

function visit(node: ts.Node, fn: (n: ts.Node) => void): void {
  fn(node);
  ts.forEachChild(node, (c) => visit(c, fn));
}

export function topDir(rel: string): string {
  return rel.split("/")[0];
}

// ---------------- 模块说明符 ----------------

export function moduleSpecifiers(f: SourceFile): string[] {
  const sf = parse(f);
  const out: string[] = [];
  visit(sf, (n) => {
    if ((ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) && n.moduleSpecifier && ts.isStringLiteral(n.moduleSpecifier)) {
      out.push(n.moduleSpecifier.text);
    } else if (ts.isCallExpression(n) && n.arguments.length > 0 && ts.isStringLiteral(n.arguments[0])) {
      const callee = n.expression;
      if ((ts.isIdentifier(callee) && callee.text === "require") || callee.kind === ts.SyntaxKind.ImportKeyword) {
        out.push(n.arguments[0].text);
      }
    }
  });
  return out;
}

const NODE_BUILTINS = new Set(["fs", "path", "child_process", "http", "https", "net", "dgram", "os", "url", "stream", "crypto", "worker_threads", "tls", "dns", "http2", "module", "vm", "util", "events", "buffer", "process", "zlib"]);

function isNodeBuiltin(spec: string): boolean {
  return spec.startsWith("node:") || NODE_BUILTINS.has(spec.split("/")[0]);
}

/** 相对导入落在 src/ 下的哪个顶层目录 */
function relTarget(fileRel: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null;
  const abs = resolve(SRC, dirname(fileRel), spec);
  return topDir(relative(SRC, abs).split("\\").join("/"));
}

/** §5.2 import 纪律表：返回违规描述列表（空 = 合规）。 */
export function importViolations(f: SourceFile): string[] {
  const dir = topDir(f.rel);
  const bad: string[] = [];
  for (const spec of moduleSpecifiers(f)) {
    const target = relTarget(f.rel, spec);
    if (target !== null) {
      const ok =
        target === dir ||
        (target === "shared" && ["renderer", "host", "main"].includes(dir));
      if (!ok) bad.push(`${f.rel}: 跨目录导入 ${spec}`);
      continue;
    }
    switch (dir) {
      case "shared":
        bad.push(`${f.rel}: shared/ 只允许纯 TS，禁止 ${spec}`);
        break;
      case "renderer":
        if (!/^(react|react-dom|markdown-it)(\/|$)/.test(spec)) bad.push(`${f.rel}: renderer/ 禁止 ${spec}`);
        break;
      case "preload":
        if (spec !== "electron") bad.push(`${f.rel}: preload/ 只允许 electron，禁止 ${spec}`);
        break;
      case "host": {
        const allowed = ["node:fs", "node:fs/promises", "node:path"];
        if (spec === "node:child_process" && f.rel === "host/spawner.ts") break;
        if (!allowed.includes(spec)) bad.push(`${f.rel}: host/ 禁止 ${spec}`);
        break;
      }
      case "main":
        if (!["electron", "node:fs", "node:path", "node:stream"].includes(spec)) bad.push(`${f.rel}: main/ 禁止 ${spec}`);
        break;
      default:
        if (isNodeBuiltin(spec) || spec === "electron") bad.push(`${f.rel}: 未归类目录导入 ${spec}`);
    }
  }
  // 非导入形式的越界：host 不许调 fetch；shared 不许碰 DOM 全局
  const sf = parse(f);
  visit(sf, (n) => {
    if (!ts.isIdentifier(n)) return;
    const parent = n.parent;
    const isPropName = parent && ts.isPropertyAccessExpression(parent) && parent.name === n;
    if (isPropName) return;
    if (dir === "host" && n.text === "fetch") bad.push(`${f.rel}: host/ 禁止 fetch`);
    if (dir === "shared" && ["window", "document", "navigator", "localStorage"].includes(n.text)) {
      bad.push(`${f.rel}: shared/ 禁止 DOM 全局 ${n.text}`);
    }
  });
  if (dir === "preload") {
    visit(sf, (n) => {
      if (ts.isImportDeclaration(n) && n.importClause?.namedBindings && ts.isNamedImports(n.importClause.namedBindings)) {
        for (const el of n.importClause.namedBindings.elements) {
          if (el.name.text !== "ipcRenderer") bad.push(`${f.rel}: preload/ 只允许 ipcRenderer，禁止 ${el.name.text}`);
        }
      }
    });
  }
  return bad;
}

// ---------------- TG-2：fs 写类 API ----------------

const FS_WRITE = new Set([
  "writeFile", "writeFileSync", "appendFile", "appendFileSync", "mkdir", "mkdirSync", "mkdtemp", "mkdtempSync",
  "rename", "renameSync", "unlink", "unlinkSync", "rm", "rmSync", "rmdir", "rmdirSync", "copyFile", "copyFileSync",
  "cp", "cpSync", "createWriteStream", "truncate", "truncateSync", "ftruncate", "ftruncateSync", "utimes", "utimesSync",
  "lutimes", "lutimesSync", "futimes", "futimesSync", "chmod", "chmodSync", "lchmod", "lchmodSync", "chown", "chownSync",
  "lchown", "lchownSync", "symlink", "symlinkSync", "link", "linkSync", "writeSync", "writev", "writevSync",
]);

export function fsWriteCalls(f: SourceFile): string[] {
  const sf = parse(f);
  const hits: string[] = [];
  visit(sf, (n) => {
    if (ts.isCallExpression(n)) {
      const c = n.expression;
      const name = ts.isPropertyAccessExpression(c) ? c.name.text : ts.isIdentifier(c) ? c.text : null;
      if (name && FS_WRITE.has(name)) hits.push(`${f.rel}: ${name}()`);
      // open/openSync 的 flags 只许是 "r"
      if ((name === "open" || name === "openSync") && ts.isPropertyAccessExpression(c)) {
        const flag = n.arguments[1];
        if (flag && !(ts.isStringLiteral(flag) && flag.text === "r")) hits.push(`${f.rel}: ${name}(…, 非 "r")`);
      }
    }
    if (ts.isImportSpecifier(n) && FS_WRITE.has((n.propertyName ?? n.name).text)) hits.push(`${f.rel}: import ${(n.propertyName ?? n.name).text}`);
  });
  return hits;
}

// ---------------- TG-4′ / TG-10：答复类与发送类方法的点击纪律（Spec 10 §2.4 第 1 层） ----------------

/** 方法 → （唯一允许的文件，允许的事件属性）。这张表就是 Spec 10 §2.4 第 1 层的闭集。 */
export const ACTION_METHODS: Record<string, { file: string; attrs: readonly string[] }> = {
  "approval.decide": { file: "renderer/HumanCards.tsx", attrs: ["onClick"] },
  "conv.answer": { file: "renderer/HumanCards.tsx", attrs: ["onClick"] },
  "conv.send": { file: "renderer/Composer.tsx", attrs: ["onClick", "onKeyDown"] },
  "conv.interrupt": { file: "renderer/Composer.tsx", attrs: ["onClick", "onKeyDown"] },
  "conv.command": { file: "renderer/SessionHeader.tsx", attrs: ["onClick"] },
  "conv.end": { file: "renderer/SessionHeader.tsx", attrs: ["onClick"] },
  "conv.resume": { file: "renderer/SessionHeader.tsx", attrs: ["onClick"] },
  "episode.create": { file: "renderer/NewEpisodeForm.tsx", attrs: ["onClick"] },
};

const LOOP_KINDS = [
  ts.SyntaxKind.ForStatement,
  ts.SyntaxKind.ForInStatement,
  ts.SyntaxKind.ForOfStatement,
  ts.SyntaxKind.WhileStatement,
  ts.SyntaxKind.DoStatement,
];
const ITER_METHODS = new Set(["map", "forEach", "reduce", "filter", "some", "every", "flatMap", "find"]);

function isFunctionLike(n: ts.Node): n is ts.FunctionLikeDeclaration {
  return (
    ts.isFunctionDeclaration(n) || ts.isFunctionExpression(n) || ts.isArrowFunction(n) || ts.isMethodDeclaration(n) ||
    ts.isGetAccessorDeclaration(n) || ts.isSetAccessorDeclaration(n) || ts.isConstructorDeclaration(n)
  );
}

function enclosingFunction(n: ts.Node): ts.FunctionLikeDeclaration | null {
  for (let p = n.parent; p; p = p.parent) if (isFunctionLike(p)) return p;
  return null;
}

/** 这个函数是不是直接挂在允许的 JSX 事件属性上，且元素标签是小写原生元素。 */
function jsxEventOf(fn: ts.FunctionLikeDeclaration): { attr: string; native: boolean } | null {
  const expr = fn.parent;
  if (!expr || !ts.isJsxExpression(expr)) return null;
  const attr = expr.parent;
  if (!attr || !ts.isJsxAttribute(attr) || attr.initializer !== expr) return null;
  const opening = attr.parent?.parent; // JsxAttributes → JsxOpeningElement / JsxSelfClosingElement
  if (!opening || !(ts.isJsxOpeningElement(opening) || ts.isJsxSelfClosingElement(opening))) return null;
  const tag = opening.tagName;
  return { attr: attr.name.getText(), native: ts.isIdentifier(tag) && /^[a-z]/.test(tag.text) };
}

export function actionClickViolations(f: SourceFile): string[] {
  // 纪律只针对 renderer（UI 里能把答复发出去的地方）；host/ 的方法名出现在分派表里是正常的
  if (topDir(f.rel) !== "renderer") return [];
  const sf = parse(f);
  const bad: string[] = [];
  const countedHandlers = new Set<ts.Node>();
  visit(sf, (n) => {
    if (!ts.isStringLiteralLike(n)) return;
    const spec = ACTION_METHODS[n.text];
    if (!spec) return;
    if (f.rel !== spec.file) {
      bad.push(`${f.rel}: ${n.text} 出现在禁写文件（只许 ${spec.file}）`);
      return;
    }
    const fn = enclosingFunction(n);
    if (!fn) {
      bad.push(`${f.rel}: ${n.text} 的调用点不在任何函数里`);
      return;
    }
    const jsx = jsxEventOf(fn);
    if (!jsx || !spec.attrs.includes(jsx.attr)) {
      bad.push(`${f.rel}: ${n.text} 的最近外层函数不是 ${spec.attrs.join("/")} 的值函数`);
      return;
    }
    if (!jsx.native) {
      bad.push(`${f.rel}: ${jsx.attr} 挂在自定义组件上（须是小写原生元素）`);
      return;
    }
    // 首句：if (!<第一形参>.nativeEvent.isTrusted) return;
    const body = fn.body;
    if (!body || !ts.isBlock(body)) {
      bad.push(`${f.rel}: ${n.text} 的处理器没有函数体，无法检查 isTrusted 首句`);
      return;
    }
    const first = body.statements[0];
    const guard = first && ts.isIfStatement(first) ? /^!\s*([A-Za-z_$][\w$]*)\.nativeEvent\.isTrusted$/.exec(first.expression.getText(sf)) : null;
    const param = fn.parameters[0]?.name.getText(sf);
    const isBareReturn = !!first && ts.isIfStatement(first) && ts.isReturnStatement(first.thenStatement) && first.thenStatement.expression === undefined;
    if (!guard || !isBareReturn || guard[1] !== param) {
      bad.push(`${f.rel}: ${n.text} 的处理器首句不是对第一个形参的 nativeEvent.isTrusted 检查`);
      return;
    }
    // 处理器体内：无循环、无迭代方法
    let loop = false;
    const scan = (node: ts.Node) => {
      if (LOOP_KINDS.includes(node.kind)) loop = true;
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && ITER_METHODS.has(node.expression.name.text)) loop = true;
      ts.forEachChild(node, scan);
    };
    ts.forEachChild(body, scan);
    if (loop) {
      bad.push(`${f.rel}: ${n.text} 的处理器体内有循环或迭代方法调用`);
      return;
    }
    // 每个处理器里闭集方法的调用合计至多一处（防把两次答复展开连写）；同一处理器只计一次
    if (!countedHandlers.has(fn)) {
      countedHandlers.add(fn);
      let count = 0;
      const countCalls = (node: ts.Node) => {
        if (ts.isStringLiteralLike(node) && ACTION_METHODS[node.text]) count += 1;
        ts.forEachChild(node, countCalls);
      };
      ts.forEachChild(body, countCalls);
      if (count > 1) bad.push(`${f.rel}: 同一个处理器里出现了多处闭集方法调用`);
    }
  });
  return bad;
}

// ---------------- TG-13：markdown 与危险 HTML 只在 PreviewPane.tsx ----------------

export const PREVIEW_FILE = "renderer/PreviewPane.tsx";

export function markdownViolations(f: SourceFile): string[] {
  const bad: string[] = [];
  if (f.rel === PREVIEW_FILE) return bad;
  if (moduleSpecifiers(f).some((s) => s === "markdown-it" || s.startsWith("markdown-it/"))) bad.push(`${f.rel}: 只有 PreviewPane.tsx 能 import markdown-it`);
  if (f.text.includes("dangerouslySetInnerHTML")) bad.push(`${f.rel}: 只有 PreviewPane.tsx 能用 dangerouslySetInnerHTML`);
  return bad;
}

// ---------------- TG-14：桌面端不读会话记录 ----------------

export function sessionLogViolations(): string[] {
  return walk(SRC).filter((p) => !/(^|\/)index\.html$/.test(p) && readFileSync(p, "utf-8").includes("session.jsonl")).map((p) => `${relative(SRC, p)}: 出现 session.jsonl 字面量`);
}

// ---------------- TG-15：建期表单的期名初值不从对话预填 ----------------

export function newEpisodeFormViolations(f: SourceFile): string[] {
  if (f.rel !== "renderer/NewEpisodeForm.tsx") return [];
  const sf = parse(f);
  const bad: string[] = [];
  if (!/useState\(\s*""\s*\)/.test(f.text)) bad.push(`${f.rel}: 期名初值不是空串字面量 useState("")`);
  let found = false;
  visit(sf, (n) => {
    if (!ts.isFunctionDeclaration(n) || n.name?.text !== "NewEpisodeForm") return;
    found = true;
    const param = n.parameters[0];
    if (param) {
      const t = param.type;
      if (!t || !ts.isTypeLiteralNode(t)) {
        bad.push(`${f.rel}: props 类型必须就地写明（否则无法检查是否含 string 字段）`);
        return;
      }
      for (const m of t.members) {
        if (!ts.isPropertySignature(m) || !m.type) continue;
        const text = m.type.getText(sf).trim();
        // 只禁「能装下对话原文」的字段：没有任何 string 类型的 prop 能承载期名预填
        if (/^string(\s*\|\s*null)?$/.test(text)) bad.push(`${f.rel}: 存在 string 类型的 prop —— 可能承载对话预填`);
      }
    }
  });
  if (!found) bad.push(`${f.rel}: 找不到 NewEpisodeForm 组件`);
  return bad;
}

// ---------------- TG-16：rpc.call 的首参必须是方法闭集字面量 ----------------

export const RPC_CALL_EXEMPT = "renderer/testHooks.ts";

export function rpcCallViolations(f: SourceFile, methods: readonly string[]): string[] {
  if (f.rel === RPC_CALL_EXEMPT) return [];
  const sf = parse(f);
  const bad: string[] = [];
  visit(sf, (n) => {
    if (!ts.isPropertyAccessExpression(n) || n.name.text !== "call") return;
    const call = n.parent;
    if (!ts.isCallExpression(call) || call.expression !== n) {
      bad.push(`${f.rel}: rpc.call 不是直接被调用（bind/apply/赋值都不许）`);
      return;
    }
    const first = call.arguments[0];
    if (!first || !ts.isStringLiteralLike(first) || !methods.includes(first.text)) {
      bad.push(`${f.rel}: rpc.call 的首参不是方法闭集里的字符串字面量`);
    }
  });
  return bad;
}

// ---------------- TG-17：卡片的 key ----------------

export function cardKeyViolations(f: SourceFile): string[] {
  if (f.rel !== "renderer/HumanCards.tsx") return [];
  const sf = parse(f);
  const bad: string[] = [];
  visit(sf, (n) => {
    if (!ts.isJsxOpeningElement(n) && !ts.isJsxSelfClosingElement(n)) return;
    const tag = n.tagName.getText();
    const want = tag === "RequestCard" ? "request_id" : tag === "StopPointCard" ? "approval_id" : null;
    if (!want) return;
    const key = n.attributes.properties.find((a): a is ts.JsxAttribute => ts.isJsxAttribute(a) && a.name.getText() === "key");
    if (!key) {
      bad.push(`${f.rel}: <${tag}> 没有 key`);
      return;
    }
    if (!key.initializer?.getText().includes(want)) bad.push(`${f.rel}: <${tag}> 的 key 不是 ${want}`);
  });
  return bad;
}

// ---------------- TG-6：测试钩子必须被 isPackaged 守卫 ----------------

const HOOK_IDENT = /^(testHook|installTestHooks|handshakeHook|__avaTest\w*)$/;

function guardedByIsPackaged(n: ts.Node, sf: ts.SourceFile): boolean {
  let child: ts.Node = n;
  let p: ts.Node | undefined = n.parent;
  while (p) {
    if (ts.isIfStatement(p) && p.thenStatement === child && /!\s*[\w.]*isPackaged\b/.test(p.expression.getText(sf))) return true;
    if (ts.isIfStatement(p) && p.expression === child && /!\s*[\w.]*isPackaged\b/.test(p.expression.getText(sf))) return true;
    if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken && p.right === child && /!\s*[\w.]*isPackaged\b/.test(p.left.getText(sf))) return true;
    if (ts.isConditionalExpression(p) && p.whenTrue === child && /!\s*[\w.]*isPackaged\b/.test(p.condition.getText(sf))) return true;
    // 函数体首句为 `if (<isPackaged>) return;` 的函数，整个函数体视为已守卫
    if ((ts.isFunctionDeclaration(p) || ts.isMethodDeclaration(p) || ts.isArrowFunction(p) || ts.isFunctionExpression(p)) && p.body && ts.isBlock(p.body)) {
      const first = p.body.statements[0];
      if (first && ts.isIfStatement(first) && /(?<!!)\b[\w.]*isPackaged\b/.test(first.expression.getText(sf)) && !first.expression.getText(sf).includes("!") && ts.isReturnStatement(first.thenStatement)) return true;
    }
    child = p;
    p = p.parent;
  }
  return false;
}

export function unguardedHooks(f: SourceFile): string[] {
  const sf = parse(f);
  const bad: string[] = [];
  visit(sf, (n) => {
    let marker: string | null = null;
    if (ts.isStringLiteralLike(n) && n.text.startsWith("--ava-")) marker = n.text;
    else if (ts.isIdentifier(n) && HOOK_IDENT.test(n.text)) {
      const p = n.parent;
      const isDecl =
        (p && (ts.isPropertySignature(p) || ts.isPropertyAssignment(p) || ts.isFunctionDeclaration(p) || ts.isMethodDeclaration(p) || ts.isVariableDeclaration(p) || ts.isImportSpecifier(p) || ts.isExportSpecifier(p) || ts.isPropertyDeclaration(p))) &&
        (p as { name?: ts.Node }).name === n;
      if (!isDecl) marker = n.text;
    }
    if (marker && !guardedByIsPackaged(n, sf)) bad.push(`${f.rel}: 测试钩子 ${marker} 未被 isPackaged 守卫`);
  });
  return bad;
}

// ---------------- TG-8 / TG-9 ----------------

export function forbiddenElectronApis(f: SourceFile): string[] {
  const sf = parse(f);
  const bad: string[] = [];
  visit(sf, (n) => {
    if (ts.isIdentifier(n) && n.text === "autoUpdater") bad.push(`${f.rel}: autoUpdater`);
    if (ts.isPropertyAccessExpression(n) && n.name.text === "start" && n.expression.getText(sf).endsWith("crashReporter")) {
      bad.push(`${f.rel}: crashReporter.start`);
    }
  });
  return bad;
}

export function jsonStringifyCalls(f: SourceFile): string[] {
  const sf = parse(f);
  const hits: string[] = [];
  visit(sf, (n) => {
    if (ts.isPropertyAccessExpression(n) && n.name.text === "stringify" && n.expression.getText(sf) === "JSON") hits.push(f.rel);
    if (ts.isElementAccessExpression(n) && n.expression.getText(sf) === "JSON") hits.push(`${f.rel}（下标访问）`);
  });
  return hits;
}

// ---------------- TG-11：入站帧的构造点只在 host/sessions.ts ----------------

const INBOUND_TYPES = new Set(["user_message", "answer", "interrupt", "command", "shutdown"]);

/** 对象字面量里属性名为 t、值为入站类型字符串的构造点（AST 匹配属性赋值，不按裸字符串匹配）。 */
export function inboundFrameOwners(files: readonly SourceFile[]): { file: string; t: string }[] {
  const hits: { file: string; t: string }[] = [];
  for (const f of files) {
    const sf = parse(f);
    visit(sf, (n) => {
      if (!ts.isObjectLiteralExpression(n)) return;
      for (const prop of n.properties) {
        if (!ts.isPropertyAssignment(prop)) continue;
        const name = ts.isIdentifier(prop.name) || ts.isStringLiteral(prop.name) ? prop.name.text : null;
        if (name !== "t") continue;
        if (ts.isStringLiteralLike(prop.initializer) && INBOUND_TYPES.has(prop.initializer.text)) hits.push({ file: f.rel, t: prop.initializer.text });
      }
    });
  }
  return hits;
}

// ---------------- TG-12：KEYCHAIN_READ 只在 spawner.ts（定义）与 secrets.ts（唯一调用） ----------------

export function keychainTemplateViolations(files: readonly SourceFile[]): string[] {
  const bad: string[] = [];
  for (const f of files) {
    if (f.rel === "host/spawner.ts" || f.rel === "host/secrets.ts") continue;
    const sf = parse(f);
    visit(sf, (n) => {
      if (ts.isStringLiteralLike(n) && n.text === "KEYCHAIN_READ") bad.push(`${f.rel}: KEYCHAIN_READ`);
    });
  }
  return bad;
}
