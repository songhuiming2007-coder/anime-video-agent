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
  if (!repoRoot.startsWith("/")) return "请选择绝对路径目录";
  const py = probe.readText(`${repoRoot}/pyproject.toml`);
  if (py === null) return "所选目录不是 anime-video-agent 仓库（找不到 pyproject.toml）";
  if (!/^name\s*=\s*"anime-video-agent"\s*$/m.test(py)) return "所选目录不是 anime-video-agent 仓库（pyproject.toml 名称不匹配）";
  if (!probe.isExecutable(pythonPathOf(repoRoot))) return "缺少虚拟环境（.venv/bin/python 不可执行，请先在仓库目录运行 uv sync）";
  if (!probe.exists(`${repoRoot}/pipeline/agent/cli.py`)) return "仓库内容不完整（找不到 pipeline/agent/cli.py）";
  return null;
}
