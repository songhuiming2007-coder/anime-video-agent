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

// ---------------- TG-4：approval.decide 只在决策条的 onClick 里 ----------------

export const DECISION_BAR_FILE = "renderer/DecisionBar.tsx";

export function decideCallViolations(f: SourceFile): string[] {
  const sf = parse(f);
  const bad: string[] = [];
  visit(sf, (n) => {
    if (!ts.isStringLiteralLike(n) || n.text !== "approval.decide") return;
    if (f.rel !== DECISION_BAR_FILE) {
      bad.push(`${f.rel}: approval.decide 出现在决策条组件之外`);
      return;
    }
    let p: ts.Node | undefined = n.parent;
    let inOnClick = false;
    while (p) {
      if (ts.isJsxAttribute(p) && p.name.getText(sf) === "onClick") {
        inOnClick = true;
        break;
      }
      p = p.parent;
    }
    if (!inOnClick) bad.push(`${f.rel}: approval.decide 的调用点祖先不是 onClick 属性值函数`);
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
