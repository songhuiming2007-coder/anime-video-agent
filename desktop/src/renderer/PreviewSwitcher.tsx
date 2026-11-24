// D49-A S4/S5 + D169：预览区顶部的一站式文件切换器（完整本期产物下拉 + 子目录内联展开 + 镜头画廊 + 紧凑封面导入）。
import { useCallback, useEffect, useRef, useState } from "react";
import { groupArtifacts, type SwitchItem } from "../shared/artifactGroups";
import type { ShotsEntry, TreeEntry } from "../shared/protocol";
import { CoverImport } from "./CoverImport";
import { FloatingPopover } from "./floating";
import { Icon, type IconName } from "./icons";
import { Disclosure } from "./motion";
import { previewKind } from "../shared/previewKind";
import type { PreviewTarget } from "./PreviewPane";
import { errText, type RpcClient } from "./rpc";
import { Badge } from "./ui";

const KIND_ICON: Record<string, IconName> = { video: "film", audio: "wave", image: "image", html: "file" };

export function PreviewSwitcher({
  epKey,
  rpc,
  current,
  running,
  treeBump = 0,
  reachOk = true,
  onPick,
  onCoverImported,
}: {
  epKey: string | null;
  rpc: RpcClient;
  current: PreviewTarget | null;
  running: readonly (string | null)[];
  treeBump?: number;
  reachOk?: boolean;
  onPick: (t: PreviewTarget) => void;
  onCoverImported?: () => void;
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
  /** 镜头画廊 */
  const [galleriesOpen, setGalleriesOpen] = useState(false);
  const [galleries, setGalleries] = useState<ShotsEntry[] | null>(null);
  const [coverNotice, setCoverNotice] = useState<string | null>(null);

  const loadRoot = useCallback(() => {
    if (!epKey) {
      setEntries(null);
      return;
    }
    setErr(null);
    rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "" }).then(setEntries, (e) => setErr(errText(e)));
  }, [epKey, rpc]);

  // 切期或切往选题/企划视图时清空上一期残留状态（coverNotice 不随 treeBump 清空）
  useEffect(() => {
    setCoverNotice(null);
    setSubOpen({});
    setSubTrees({});
  }, [epKey]);

  // 切期、导入封面(treeBump)或恢复可达时刷新顶层产物目录
  useEffect(() => {
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
                  <span className="ui-chev" data-open={subOpen[sub.rel] ? "1" : "0"}><Icon name="chevron-right" size="sm" /></span>
                </button>
              )}
            </div>
            {sub.kind === "dir" && (
              <Disclosure open={!!subOpen[sub.rel]}>
                {() => renderSubEntries(sub.rel, depth + 1)}
              </Disclosure>
            )}
          </div>
        ))}
      </div>
    );
  };

  const allItems = entries ? groupArtifacts(entries, running) : [];

  return (
    <div className="preview-switch-wrap" data-testid="preview-switch">
      <div className="preview-switch">
        {epKey !== null && (
          <FloatingPopover
            open={open}
            onOpenChange={(next) => {
              if (next) setGalleriesOpen(false);
              setOpen(next);
            }}
            placement="bottom-start"
            className="ui-popover preview-switch-pop"
            testId="preview-switch-pop"
            renderTrigger={(trigger) => (
              <button
                type="button"
                ref={trigger.ref}
                {...trigger.props}
                className="ui-btn ui-btn--ghost ui-btn--sm preview-switch-btn"
                aria-expanded={open}
                data-testid="preview-switch-btn"
              >
                <Icon name="folder" size="sm" />
                <span className="preview-switch-label">{label}</span>
                {curRel === "02-script.md" && <Badge>可编辑</Badge>}
                <Icon name="chevron-down" size="sm" />
              </button>
            )}
            renderContent={() => (
              <>
                {pickErr !== null && <div className="warn" data-testid="preview-switch-pick-err">{pickErr}</div>}
                {err !== null && <div className="muted">{err}</div>}
                {entries === null && err === null && (
                  <div className="ui-skeleton-stack">
                    <div className="ui-skeleton ui-skeleton--lg" />
                    <div className="ui-skeleton ui-skeleton--md" />
                    <div className="ui-skeleton ui-skeleton--sm" />
                  </div>
                )}
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
                                <span className="ui-chev" data-open={subOpen[it.rel] ? "1" : "0"}><Icon name="chevron-right" size="sm" /></span>
                              </button>
                            )}
                          </div>
                          {it.kind === "dir" && !it.missing && (
                            <Disclosure open={!!subOpen[it.rel]}>
                              {() => renderSubEntries(it.rel, 1)}
                            </Disclosure>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
              </>
            )}
          />
        )}
        {epKey === null && <span className="preview-switch-label muted">{label}</span>}
        <span className="spacer" />
        {epKey !== null && (
          <CoverImport
            epKey={epKey}
            rpc={rpc}
            onImported={() => onCoverImported?.()}
            onNotice={setCoverNotice}
          />
        )}
        <FloatingPopover
          open={galleriesOpen}
          onOpenChange={(next) => {
            if (next) setOpen(false);
            setGalleriesOpen(next);
          }}
          placement="bottom-end"
          className="ui-popover preview-switch-pop preview-galleries-pop"
          testId="galleries-pop"
          renderTrigger={(trigger) => (
            <button
              type="button"
              ref={trigger.ref}
              {...trigger.props}
              className="ui-btn ui-btn--ghost ui-btn--sm preview-galleries-btn"
              aria-expanded={galleriesOpen}
              data-testid="galleries-toggle"
            >
              <Icon name="image" size="sm" />
              镜头画廊
              {galleries && <span className="ui-count">{galleries.length}</span>}
            </button>
          )}
          renderContent={() => (
            <>
              <div className="ui-popover-title preview-switch-gtitle">镜头画廊</div>
              {galleries === null && (
                <div className="ui-skeleton-stack">
                  <div className="ui-skeleton ui-skeleton--md" />
                  <div className="ui-skeleton ui-skeleton--sm" />
                </div>
              )}
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
            </>
          )}
        />
      </div>
      {epKey !== null && coverNotice !== null && (
        <div className="notice preview-cover-notice ui-confirm-enter" data-testid="cover-import-msg">
          <Icon name="info" size="sm" />
          {coverNotice}
        </div>
      )}
    </div>
  );
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
