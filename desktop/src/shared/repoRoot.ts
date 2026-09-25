// repoRoot 形状校验（Spec 8 §3.6）：pyproject.toml 含 name = "anime-video-agent"、.venv/bin/python 可执行、pipeline/agent/cli.py 存在。
// 纯函数，文件系统经注入：main 的二次确认框（显示校验结果）与 host 的生效前复核共用同一份规则。
export interface RepoRootProbe {
  /** 读文本；不存在或不可读返回 null */
  readText(p: string): string | null;
  isExecutable(p: string): boolean;
  exists(p: string): boolean;
}

export function pythonPathOf(repoRoot: string): string {
  return `${repoRoot}/.venv/bin/python`;
}

/** 通过 → null；否则返回人读的原因 */
export function repoRootProblem(repoRoot: string, probe: RepoRootProbe): string | null {
  if (!repoRoot.startsWith("/")) return "不是绝对路径";
  const py = probe.readText(`${repoRoot}/pyproject.toml`);
  if (py === null) return "pyproject.toml 不存在";
  if (!/^name\s*=\s*"anime-video-agent"\s*$/m.test(py)) return "pyproject.toml 的 name 不是 anime-video-agent";
  if (!probe.isExecutable(pythonPathOf(repoRoot))) return ".venv/bin/python 不可执行";
  if (!probe.exists(`${repoRoot}/pipeline/agent/cli.py`)) return "pipeline/agent/cli.py 不存在";
  return null;
}
