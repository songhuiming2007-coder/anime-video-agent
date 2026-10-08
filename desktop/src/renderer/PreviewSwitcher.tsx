// D49-A S4/S5：预览区顶部的文件切换器。侧栏收起（做视频时的常态）也能选本期任何产物；
// 可编辑的定稿标出来（人不知道能在应用里改稿，D49 ③）；运行中作业正在产出的产物标「生成中」。
import { useEffect, useRef, useState } from "react";
import { groupArtifacts, type SwitchItem } from "../shared/artifactGroups";
import type { TreeEntry } from "../shared/protocol";
import { Icon } from "./icons";
import type { PreviewTarget } from "./PreviewPane";
import { errText, type RpcClient } from "./rpc";
import { Badge } from "./ui";

export function PreviewSwitcher({
  epKey,
  rpc,
  current,
  running,
  onPick,
}: {
  epKey: string;
  rpc: RpcClient;
  current: PreviewTarget | null;
  running: readonly (string | null)[];
  onPick: (t: PreviewTarget) => void;
}) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "" }).then(setEntries, (e) => setErr(errText(e)));
    const away = (ev: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(ev.target as Node)) setOpen(false);
    };
    const esc = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, epKey, rpc]);

  const curRel = current && current.root === "episodes" ? current.rel.replace(new RegExp(`^${escapeRe(epKey)}/`), "") : null;
  const label = curRel === null || curRel === "" ? "选择本期文件" : curRel;

  const pick = (it: SwitchItem) => {
    if (it.missing) return;
    setOpen(false);
    if (it.kind === "dir") {
      rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: it.rel }).then(
        (sub) => onPick({ kind: "dir", root: "episodes", rel: `${epKey}/${it.rel}`, entries: sub.filter((x) => x.kind === "file").map((x) => x.name) }),
        () => undefined,
      );
    } else if (it.kind === "file") {
      const size = entries?.find((x) => x.rel === it.rel)?.size ?? null;
      onPick({ kind: "file", root: "episodes", rel: `${epKey}/${it.rel}`, size });
    }
  };

  return (
    <div className="preview-switch" ref={boxRef} data-testid="preview-switch">
      <button className="ui-btn ui-btn--ghost ui-btn--sm preview-switch-btn" aria-expanded={open} data-testid="preview-switch-btn" onClick={() => setOpen((o) => !o)}>
        <span className="preview-switch-label">{label}</span>
        {curRel === "02-script.md" && <Badge>可编辑</Badge>}
        <Icon name="chevron-down" size="sm" />
      </button>
      {open && (
        <div className="ui-popover preview-switch-pop" data-testid="preview-switch-pop">
          <div className="ui-popover-title">本期文件</div>
          {err !== null && <div className="muted">{err}</div>}
          {entries === null && err === null && <div className="muted">…</div>}
          {entries !== null &&
            groupArtifacts(entries, running).map((g) => (
              <div key={g.title} className="preview-switch-group">
                <div className="preview-switch-gtitle">{g.title}</div>
                {g.items.map((it) => (
                  <button
                    key={it.rel}
                    className="ui-row preview-switch-row"
                    aria-current={curRel === it.rel ? "true" : undefined}
                    disabled={it.missing}
                    data-testid="preview-switch-row"
                    data-rel={it.rel}
                    onClick={() => pick(it)}
                  >
                    <Icon name={it.kind === "dir" ? "folder" : "file"} size="sm" />
                    <span className="ui-row-title">{it.name}</span>
                    <span className="preview-switch-meta">
                      {it.missing ? "未生成" : it.generating ? <Badge tone="wait">生成中</Badge> : it.editable ? "✎ 可在此编辑" : ""}
                    </span>
                  </button>
                ))}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
