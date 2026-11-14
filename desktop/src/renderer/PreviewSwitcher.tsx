// D49-A S4/S5 + D169：预览区顶部的一站式文件切换器（彻底取代左侧栏产物树与画廊列表）。
// 包含：① 当前产物下拉按钮 + 子目录内联展开（如 07-cover/c1.png、03-audio/*.wav）；
// ② 镜头画廊弹出选择器（data/library/shots/*.html）；
// ③ 核心阶段快捷胶囊（01 选题 / 02 稿件 / 03 配音 / 04 审看 / 05 成片）。
import { useCallback, useEffect, useRef, useState } from "react";
import { groupArtifacts, type SwitchItem } from "../shared/artifactGroups";
import type { ShotsEntry, TreeEntry } from "../shared/protocol";
import { Icon, type IconName } from "./icons";
import { previewKind } from "../shared/previewKind";
import type { PreviewTarget } from "./PreviewPane";
import { errText, type RpcClient } from "./rpc";
import { Badge } from "./ui";

const STAGE_CHIPS: readonly { name: string; label: string; kind: "file" | "dir" }[] = [
  { name: "01-topic.md", label: "01 选题", kind: "file" },
  { name: "02-script.md", label: "02 稿件", kind: "file" },
  { name: "03-audio", label: "03 配音", kind: "dir" },
  { name: "04-review.html", label: "04 审看", kind: "file" },
  { name: "05-final.mp4", label: "05 成片", kind: "file" },
];

const KIND_ICON: Record<string, IconName> = { video: "film", audio: "wave", image: "image", html: "file" };

export function PreviewSwitcher({
  epKey,
  rpc,
  current,
  running,
  treeBump = 0,
  reachOk = true,
  onPick,
}: {
  epKey: string | null;
  rpc: RpcClient;
  current: PreviewTarget | null;
  running: readonly (string | null)[];
  treeBump?: number;
  reachOk?: boolean;
  onPick: (t: PreviewTarget) => void;
}) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  /** 上一次选目录失败的原因（与列表加载错误分开：重新展开会清掉 err，这条要留到下一次选择） */
  const [pickErr, setPickErr] = useState<string | null>(null);
  /** 下拉面板内展开的子目录（rel -> TreeEntry[] | "loading" | string 错误） */
  const [subOpen, setSubOpen] = useState<Record<string, boolean>>({});
  const subOpenRef = useRef(subOpen);
  subOpenRef.current = subOpen;
  const [subTrees, setSubTrees] = useState<Record<string, TreeEntry[] | "loading" | string>>({});
  /** 镜头画廊（从左侧栏移到预览切换器顶栏） */
  const [galleriesOpen, setGalleriesOpen] = useState(false);
  const [galleries, setGalleries] = useState<ShotsEntry[] | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  const loadRoot = useCallback(() => {
    if (!epKey) {
      setEntries(null);
      return;
    }
    setErr(null);
    rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "" }).then(setEntries, (e) => setErr(errText(e)));
  }, [epKey, rpc]);

  // 切期、导入封面(treeBump)、打开下拉或恢复可达时刷新顶层产物目录
  useEffect(() => {
    setSubOpen({});
    setSubTrees({});
    if (!epKey || !reachOk) {
      if (!epKey) setEntries(null);
      return;
    }
    loadRoot();
  }, [epKey, treeBump, reachOk, loadRoot]);

  useEffect(() => {
    if (open && epKey) loadRoot();
  }, [open, epKey, loadRoot]);

  // 已展开的子目录在 treeBump（如新导入封面）后也刷新
  useEffect(() => {
    if (!epKey || treeBump === 0) return;
    for (const [dir, opened] of Object.entries(subOpenRef.current)) {
      if (!opened) continue;
      rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: dir }).then(
        (sub) => setSubTrees((m) => ({ ...m, [dir]: sub })),
        (e) => setSubTrees((m) => ({ ...m, [dir]: errText(e) })),
      );
    }
  }, [treeBump, epKey, rpc]);

  useEffect(() => {
    if (galleriesOpen && reachOk) {
      rpc.call<ShotsEntry[]>("shots.list").then(setGalleries, () => setGalleries([]));
    }
  }, [galleriesOpen, reachOk, rpc]);

  useEffect(() => {
    if (!open && !galleriesOpen) return;
    const away = (ev: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(ev.target as Node)) {
        setOpen(false);
        setGalleriesOpen(false);
      }
    };
    const esc = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") {
        setOpen(false);
        setGalleriesOpen(false);
      }
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, galleriesOpen]);

  const curRel = current
    ? current.root === "episodes" && epKey
      ? current.rel.replace(new RegExp(`^${escapeRe(epKey)}/`), "")
      : current.rel
    : null;
  const label = curRel === null || curRel === "" ? (epKey ? "选择本期文件" : "预览文件") : curRel;

  const pickItem = (it: { rel: string; kind: "dir" | "file" | "other"; missing?: boolean; fromDraft?: boolean; size?: number | null }) => {
    if (!epKey) return;
    if (it.missing && !it.fromDraft) return;
    setPickErr(null);
    setOpen(false);
    if (it.kind === "dir") {
      // 列目录失败要说出来：静默吞掉时人只看到「点了没反应 / 预览没切过去」（D73 ③）
      rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: it.rel }).then(
        (sub) => onPick({ kind: "dir", root: "episodes", rel: `${epKey}/${it.rel}`, entries: sub.filter((x) => x.kind === "file").map((x) => x.name) }),
        (e) => {
          setPickErr(`打不开 ${it.rel}：${errText(e)}`);
          setOpen(true);
        },
      );
    } else if (it.kind === "file") {
      const size = it.size ?? entries?.find((x) => x.rel === it.rel)?.size ?? null;
      onPick({ kind: "file", root: "episodes", rel: `${epKey}/${it.rel}`, size });
    }
  };

  const toggleSubdir = (relDir: string) => {
    if (!epKey) return;
    const next = !subOpen[relDir];
    setSubOpen((m) => ({ ...m, [relDir]: next }));
    if (next && (!subTrees[relDir] || typeof subTrees[relDir] === "string")) {
      setSubTrees((m) => ({ ...m, [relDir]: "loading" }));
      rpc.call<TreeEntry[]>("tree.list", { epKey, relDir }).then(
        (sub) => setSubTrees((m) => ({ ...m, [relDir]: sub })),
        (e) => setSubTrees((m) => ({ ...m, [relDir]: errText(e) })),
      );
    }
  };

  const renderSubEntries = (relDir: string, depth: number) => {
    const val = subTrees[relDir];
    if (val === "loading" || val === undefined) return <div className="muted preview-subdir-msg">…</div>;
    if (typeof val === "string") return <div className="warn preview-subdir-msg">{val}</div>;
    if (val.length === 0) return <div className="muted preview-subdir-msg">（空目录）</div>;
    return (
      <div className="preview-subdir-children">
        {val.map((sub) => (
          <div key={sub.rel}>
            <div className="preview-switch-item" style={{ paddingLeft: depth * 12 }}>
              <button
                className="ui-row preview-switch-row"
                aria-current={curRel === sub.rel ? "true" : undefined}
                data-testid="preview-switch-row"
                data-rel={sub.rel}
                onClick={() => pickItem(sub)}
              >
                <Icon name={sub.kind === "dir" ? "folder" : (KIND_ICON[previewKind(sub.name)] ?? "file")} size="sm" />
                <span className="ui-row-title">{sub.name}</span>
              </button>
              {sub.kind === "dir" && (
                <button
                  className="ui-btn ui-btn--ghost ui-btn--sm preview-subdir-toggle"
                  aria-expanded={!!subOpen[sub.rel]}
                  aria-label={`展开 ${sub.name}`}
                  data-testid="preview-subdir-toggle"
                  data-dir={sub.rel}
                  onClick={() => toggleSubdir(sub.rel)}
                >
                  <Icon name={subOpen[sub.rel] ? "chevron-down" : "chevron-right"} size="sm" />
                </button>
              )}
            </div>
            {sub.kind === "dir" && subOpen[sub.rel] && renderSubEntries(sub.rel, depth + 1)}
          </div>
        ))}
      </div>
    );
  };

  const entryMap = new Map((entries ?? []).map((e) => [e.name, e]));
  const hasDraft = entryMap.get("02-script.draft.md")?.kind === "file";
  const allItems = entries ? groupArtifacts(entries, running) : [];

  return (
    <div className="preview-switch-wrap" ref={boxRef} data-testid="preview-switch">
      <div className="preview-switch">
        {epKey !== null && (
          <button
            className="ui-btn ui-btn--ghost ui-btn--sm preview-switch-btn"
            aria-expanded={open}
            data-testid="preview-switch-btn"
            onClick={() => {
              setGalleriesOpen(false);
              setOpen((o) => !o);
            }}
          >
            <Icon name="folder" size="sm" />
            <span className="preview-switch-label">{label}</span>
            {curRel === "02-script.md" && <Badge>可编辑</Badge>}
            <Icon name="chevron-down" size="sm" />
          </button>
        )}
        {epKey === null && <span className="preview-switch-label muted">{label}</span>}
        <span className="spacer" />
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm preview-galleries-btn"
          aria-expanded={galleriesOpen}
          data-testid="galleries-toggle"
          onClick={() => {
            setOpen(false);
            setGalleriesOpen((o) => !o);
          }}
        >
          <Icon name="image" size="sm" />
          镜头画廊
          {galleries && <span className="ui-count">{galleries.length}</span>}
        </button>
      </div>

      {/* 核心阶段快捷切换胶囊（无需先点开下拉即可一步跳转常用产物） */}
      {epKey !== null && (
        <div className="preview-stage-chips" data-testid="preview-stage-chips">
          {STAGE_CHIPS.map((ch) => {
            const found = entryMap.get(ch.name);
            const fromDraft = ch.name === "02-script.md" && !found && hasDraft;
            const disabled = !found && !fromDraft;
            const active = curRel === ch.name;
            return (
              <button
                key={ch.name}
                className="ui-btn ui-btn--ghost ui-btn--sm preview-stage-chip"
                aria-current={active ? "true" : undefined}
                disabled={disabled}
                data-testid="preview-stage-chip"
                data-rel={ch.name}
                onClick={() =>
                  pickItem({
                    rel: ch.name,
                    kind: ch.kind,
                    missing: !found,
                    fromDraft,
                    size: found?.size ?? null,
                  })
                }
              >
                {ch.label}
              </button>
            );
          })}
        </div>
      )}

      {open && epKey !== null && (
        <div className="ui-popover preview-switch-pop" data-testid="preview-switch-pop">
          {pickErr !== null && <div className="warn" data-testid="preview-switch-pick-err">{pickErr}</div>}
          {err !== null && <div className="muted">{err}</div>}
          {entries === null && err === null && <div className="muted">…</div>}
          {entries !== null &&
            allItems.map((g) => (
              <div key={g.title} className="preview-switch-group">
                <div className="ui-popover-title preview-switch-gtitle">{g.title}</div>
                {g.items.map((it: SwitchItem) => (
                  <div key={it.rel}>
                    <div className="preview-switch-item">
                      <button
                        className="ui-row preview-switch-row"
                        aria-current={curRel === it.rel ? "true" : undefined}
                        disabled={it.missing && !it.fromDraft}
                        data-testid="preview-switch-row"
                        data-rel={it.rel}
                        onClick={() => pickItem(it)}
                      >
                        <Icon name={it.kind === "dir" ? "folder" : (KIND_ICON[previewKind(it.name)] ?? "file")} size="sm" />
                        <span className="ui-row-title">{it.name}</span>
                        <span className="preview-switch-meta">
                          {it.fromDraft ? "✎ 从草稿新建" : it.missing ? "未生成" : it.generating ? <Badge tone="wait">生成中</Badge> : it.editable ? "✎ 可在此编辑" : ""}
                        </span>
                      </button>
                      {it.kind === "dir" && !it.missing && (
                        <button
                          className="ui-btn ui-btn--ghost ui-btn--sm preview-subdir-toggle"
                          aria-expanded={!!subOpen[it.rel]}
                          aria-label={`展开 ${it.name}`}
                          data-testid="preview-subdir-toggle"
                          data-dir={it.rel}
                          onClick={() => toggleSubdir(it.rel)}
                        >
                          <Icon name={subOpen[it.rel] ? "chevron-down" : "chevron-right"} size="sm" />
                        </button>
                      )}
                    </div>
                    {it.kind === "dir" && !it.missing && subOpen[it.rel] && renderSubEntries(it.rel, 1)}
                  </div>
                ))}
              </div>
            ))}
        </div>
      )}

      {galleriesOpen && (
        <div className="ui-popover preview-switch-pop preview-galleries-pop" data-testid="galleries-pop">
          <div className="ui-popover-title preview-switch-gtitle">镜头画廊（shots）</div>
          {galleries === null && <div className="muted">…</div>}
          {galleries?.length === 0 && <div className="muted">没有画廊</div>}
          {galleries?.map((g) => (
            <button
              key={g.name}
              className="ui-row preview-switch-row"
              aria-current={current?.root === "shots" && current.rel === g.name ? "true" : undefined}
              data-testid="gallery"
              data-name={g.name}
              onClick={() => {
                setGalleriesOpen(false);
                onPick({ kind: "file", root: "shots", rel: g.name, size: g.size });
              }}
            >
              <Icon name="file" size="sm" />
              <span className="ui-row-title">{g.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
