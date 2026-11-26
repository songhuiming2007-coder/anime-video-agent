// D49-A S4/S5 + D169：预览区顶部的一站式文件切换器（完整本期产物下拉 + 子目录内联展开 + 镜头画廊 + 紧凑封面导入）。
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { groupArtifacts, type SwitchItem } from "../shared/artifactGroups";
import { previewKind } from "../shared/previewKind";
import type { ShotsEntry, TreeEntry } from "../shared/protocol";
import { CoverImport, type CoverNotice } from "./CoverImport";
import { FloatingPopover, FloatingTooltip } from "./floating";
import { Icon, type IconName } from "./icons";
import { Disclosure, useCountBump } from "./motion";
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
  repoRoot = null,
  onPick,
  onCoverImported,
}: {
  epKey: string | null;
  rpc: RpcClient;
  current: PreviewTarget | null;
  running: readonly (string | null)[];
  treeBump?: number;
  reachOk?: boolean;
  repoRoot?: string | null;
  onPick: (t: PreviewTarget) => void;
  onCoverImported?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pickErr, setPickErr] = useState<string | null>(null);
  const [subOpen, setSubOpen] = useState<Record<string, boolean>>({});
  const subOpenRef = useRef(subOpen);
  subOpenRef.current = subOpen;
  const [subTrees, setSubTrees] = useState<Record<string, TreeEntry[] | "loading" | string>>({});
  const [galleriesOpen, setGalleriesOpen] = useState(false);
  const [galleries, setGalleries] = useState<ShotsEntry[] | null>(null);
  const [galleriesErr, setGalleriesErr] = useState<string | null>(null);
  const [coverNotice, setCoverNotice] = useState<(CoverNotice & { n: number }) | null>(null);
  const epKeyRef = useRef(epKey);
  epKeyRef.current = epKey;
  const galleriesGenRef = useRef(0);
  const filesListRef = useRef<HTMLDivElement | null>(null);
  const galleriesListRef = useRef<HTMLDivElement | null>(null);
  const galleryCountBump = useCountBump(galleries?.length ?? 0);

  const loadRoot = useCallback(() => {
    const targetEp = epKey;
    if (!targetEp) {
      setEntries(null);
      return;
    }
    setErr(null);
    rpc.call<TreeEntry[]>("tree.list", { epKey: targetEp, relDir: "" }).then(
      (res) => {
        if (epKeyRef.current === targetEp) setEntries(res);
      },
      (e) => {
        if (epKeyRef.current === targetEp) setErr(errText(e));
      },
    );
  }, [epKey, rpc]);

  const loadGalleries = useCallback(() => {
    const gen = (galleriesGenRef.current += 1);
    setGalleriesErr(null);
    rpc.call<ShotsEntry[]>("shots.list").then(
      (res) => {
        if (galleriesGenRef.current === gen) setGalleries(res);
      },
      (e) => {
        if (galleriesGenRef.current === gen) setGalleriesErr(errText(e));
      },
    );
  }, [rpc]);

  // 切期或切往选题/企划视图时清空上一期残留状态
  useEffect(() => {
    setCoverNotice(null);
    setPickErr(null);
    setSubOpen({});
    setSubTrees({});
  }, [epKey]);

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
    const targetEp = epKey;
    if (!targetEp || treeBump === 0) return;
    for (const [dir, opened] of Object.entries(subOpenRef.current)) {
      if (!opened) continue;
      rpc.call<TreeEntry[]>("tree.list", { epKey: targetEp, relDir: dir }).then(
        (sub) => {
          if (epKeyRef.current === targetEp) setSubTrees((m) => ({ ...m, [dir]: sub }));
        },
        (e) => {
          if (epKeyRef.current === targetEp) setSubTrees((m) => ({ ...m, [dir]: errText(e) }));
        },
      );
    }
  }, [treeBump, epKey, rpc]);

  useEffect(() => {
    galleriesGenRef.current += 1;
    setGalleries(null);
    setGalleriesErr(null);
    if (reachOk) loadGalleries();
  }, [reachOk, repoRoot, loadGalleries]);

  useEffect(() => {
    if (galleriesOpen && reachOk) loadGalleries();
  }, [galleriesOpen, reachOk, loadGalleries]);

  const visibleRowsOf = (container: HTMLElement | null): HTMLButtonElement[] =>
    container
      ? [...container.querySelectorAll<HTMLButtonElement>(".preview-switch-row:not(:disabled)")].filter((el) => !el.closest("[inert]"))
      : [];

  const handleListKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    const rows = visibleRowsOf(e.currentTarget);
    if (rows.length === 0) return;
    e.preventDefault();
    const curIdx = rows.indexOf(document.activeElement as HTMLButtonElement);
    let nextIdx = 0;
    if (e.key === "ArrowDown") nextIdx = curIdx < 0 ? 0 : (curIdx + 1) % rows.length;
    else if (e.key === "ArrowUp") nextIdx = curIdx <= 0 ? rows.length - 1 : curIdx - 1;
    else if (e.key === "End") nextIdx = rows.length - 1;
    rows[nextIdx]?.focus();
  };

  const handleTriggerKeyDown = (
    e: KeyboardEvent<HTMLButtonElement>,
    isOpen: boolean,
    setOpenFn: (next: boolean) => void,
    listRef: { current: HTMLDivElement | null },
  ) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    if (!isOpen) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      setOpenFn(true);
      const wantLast = e.key === "ArrowUp";
      requestAnimationFrame(() => {
        const rows = visibleRowsOf(listRef.current);
        (wantLast ? rows[rows.length - 1] : rows[0])?.focus();
      });
      return;
    }
    const rows = visibleRowsOf(listRef.current);
    if (rows.length === 0) return;
    e.preventDefault();
    const target = e.key === "ArrowUp" || e.key === "End" ? rows[rows.length - 1] : rows[0];
    target?.focus();
  };

  const curRel = current
    ? current.root === "episodes" && epKey
      ? current.rel.replace(new RegExp(`^${escapeRe(epKey)}/`), "")
      : current.rel
    : null;
  const label = curRel === null || curRel === "" ? (epKey ? "选择本期文件" : "预览文件") : curRel;
  const triggerIcon: IconName =
    current === null
      ? "folder"
      : current.kind === "dir"
        ? "folder"
        : KIND_ICON[previewKind(current.rel)] ?? "file";

  const pickItem = (it: { rel: string; kind: "dir" | "file" | "other"; missing?: boolean; fromDraft?: boolean; size?: number | null }) => {
    const targetEp = epKey;
    if (!targetEp) return;
    if (it.missing && !it.fromDraft) return;
    setPickErr(null);
    setOpen(false);
    if (it.kind === "dir") {
      rpc.call<TreeEntry[]>("tree.list", { epKey: targetEp, relDir: it.rel }).then(
        (sub) => {
          if (epKeyRef.current !== targetEp) return;
          onPick({ kind: "dir", root: "episodes", rel: `${targetEp}/${it.rel}`, entries: sub.filter((x) => x.kind === "file").map((x) => x.name) });
        },
        (e) => {
          if (epKeyRef.current !== targetEp) return;
          setPickErr(`打不开 ${it.rel}：${errText(e)}`);
          setOpen(true);
        },
      );
    } else if (it.kind === "file") {
      const size = it.size ?? entries?.find((x) => x.rel === it.rel)?.size ?? null;
      onPick({ kind: "file", root: "episodes", rel: `${targetEp}/${it.rel}`, size });
    }
  };

  const toggleSubdir = (relDir: string) => {
    const targetEp = epKey;
    if (!targetEp) return;
    const next = !subOpen[relDir];
    setSubOpen((m) => ({ ...m, [relDir]: next }));
    if (next && (!subTrees[relDir] || typeof subTrees[relDir] === "string")) {
      setSubTrees((m) => ({ ...m, [relDir]: "loading" }));
      rpc.call<TreeEntry[]>("tree.list", { epKey: targetEp, relDir }).then(
        (sub) => {
          if (epKeyRef.current === targetEp) setSubTrees((m) => ({ ...m, [relDir]: sub }));
        },
        (e) => {
          if (epKeyRef.current === targetEp) setSubTrees((m) => ({ ...m, [relDir]: errText(e) }));
        },
      );
    }
  };

  const renderSubEntries = (relDir: string, depth: number) => {
    const val = subTrees[relDir];
    if (val === "loading" || val === undefined) {
      return (
        <div className="ui-skeleton-stack preview-subdir-msg" aria-label="加载子目录">
          <div className="ui-skeleton ui-skeleton--md" />
          <div className="ui-skeleton ui-skeleton--sm" />
        </div>
      );
    }
    if (typeof val === "string") return <div className="warn preview-subdir-msg">{val}</div>;
    if (val.length === 0) return <div className="muted preview-subdir-msg">（空目录）</div>;
    return (
      <div className="preview-subdir-children">
        {val.map((sub) => (
          <div key={sub.rel}>
            <div className="preview-switch-item" style={{ paddingLeft: depth * 12 }}>
              <button
                type="button"
                className="ui-row preview-switch-row"
                aria-current={curRel === sub.rel ? "true" : undefined}
                data-testid={open ? "preview-switch-row" : undefined}
                data-rel={sub.rel}
                onClick={() => pickItem(sub)}
              >
                <Icon name={sub.kind === "dir" ? "folder" : (KIND_ICON[previewKind(sub.name)] ?? "file")} size="sm" />
                <span className="ui-row-title">{sub.name}</span>
              </button>
              {sub.kind === "dir" && (
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm preview-subdir-toggle"
                  aria-expanded={!!subOpen[sub.rel]}
                  aria-label={subOpen[sub.rel] ? `收起 ${sub.name}` : `展开 ${sub.name}`}
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
              <FloatingTooltip
                label={curRel ? `切换预览文件（当前：${curRel}）` : "选择本期产物文件"}
                disabled={trigger.open}
                renderTrigger={(tip) => (
                  <button
                    type="button"
                    ref={(node) => {
                      trigger.ref(node);
                      tip.ref(node);
                    }}
                    {...tip.props}
                    {...trigger.props}
                    onKeyDown={(e) => {
                      (trigger.props.onKeyDown as ((ev: KeyboardEvent<HTMLButtonElement>) => void) | undefined)?.(e);
                      if (!e.defaultPrevented) {
                        handleTriggerKeyDown(
                          e,
                          open,
                          (next) => {
                            if (next) setGalleriesOpen(false);
                            setOpen(next);
                          },
                          filesListRef,
                        );
                      }
                    }}
                    className="ui-btn ui-btn--ghost ui-btn--sm preview-switch-btn"
                    aria-expanded={open}
                    data-testid="preview-switch-btn"
                  >
                    <Icon name={triggerIcon} size="sm" />
                    <span className="preview-switch-label">{label}</span>
                    {curRel === "02-script.md" && <Badge>可编辑</Badge>}
                    <span className="ui-chev ui-chev--down" data-open={open ? "1" : "0"}>
                      <Icon name="chevron-down" size="sm" />
                    </span>
                  </button>
                )}
              />
            )}
            renderContent={() => (
              <div ref={filesListRef} onKeyDown={handleListKeyDown}>
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
                              type="button"
                              className="ui-row preview-switch-row"
                              aria-current={curRel === it.rel ? "true" : undefined}
                              disabled={it.missing && !it.fromDraft}
                              data-testid={open ? "preview-switch-row" : undefined}
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
                                type="button"
                                className="ui-btn ui-btn--ghost ui-btn--sm preview-subdir-toggle"
                                aria-expanded={!!subOpen[it.rel]}
                                aria-label={subOpen[it.rel] ? `收起 ${it.name}` : `展开 ${it.name}`}
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
              </div>
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
            onNotice={(n) => setCoverNotice(n ? { ...n, n: Date.now() } : null)}
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
              onKeyDown={(e) => {
                (trigger.props.onKeyDown as ((ev: KeyboardEvent<HTMLButtonElement>) => void) | undefined)?.(e);
                if (!e.defaultPrevented) {
                  handleTriggerKeyDown(
                    e,
                    galleriesOpen,
                    (next) => {
                      if (next) setOpen(false);
                      setGalleriesOpen(next);
                    },
                    galleriesListRef,
                  );
                }
              }}
              className="ui-btn ui-btn--ghost ui-btn--sm preview-galleries-btn"
              aria-expanded={galleriesOpen}
              data-testid="galleries-toggle"
            >
              <Icon name="image" size="sm" />
              镜头画廊
              {galleries && (
                <span className="ui-count" data-bump={galleryCountBump ? "1" : undefined}>
                  {galleries.length}
                </span>
              )}
            </button>
          )}
          renderContent={() => (
            <div ref={galleriesListRef} onKeyDown={handleListKeyDown}>
              <div className="ui-popover-title preview-switch-gtitle">镜头画廊</div>
              {galleriesErr !== null && (
                <div className="notice notice--warn">
                  <span>读取画廊失败：{galleriesErr}</span>
                  <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" onClick={loadGalleries}>
                    重试
                  </button>
                </div>
              )}
              {galleries === null && galleriesErr === null && (
                <div className="ui-skeleton-stack">
                  <div className="ui-skeleton ui-skeleton--md" />
                  <div className="ui-skeleton ui-skeleton--sm" />
                </div>
              )}
              {galleriesErr === null && galleries?.length === 0 && <div className="muted">没有画廊</div>}
              {galleries?.map((g) => (
                <button
                  type="button"
                  key={g.name}
                  className="ui-row preview-switch-row"
                  aria-current={current?.root === "shots" && current.rel === g.name ? "true" : undefined}
                  data-testid={galleriesOpen ? "gallery" : undefined}
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
        />
      </div>
      <Disclosure key={epKey ?? "_none"} open={epKey !== null && coverNotice !== null} keepLastContent>
        {epKey !== null && coverNotice !== null && (
          <div
            key={coverNotice.n}
            className={
              coverNotice.kind === "err"
                ? "notice notice--err preview-cover-notice ui-error-shake"
                : "notice preview-cover-notice ui-confirm-enter"
            }
            data-testid="cover-import-msg"
            role={coverNotice.kind === "err" ? "alert" : "status"}
          >
            <Icon name={coverNotice.kind === "err" ? "alert" : "info"} size="sm" />
            <span>{coverNotice.text}</span>
            <span className="spacer" />
            {coverNotice.path && (
              <button
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm"
                onClick={() => {
                  onPick({ kind: "file", root: "episodes", rel: `${epKey}/${coverNotice.path}`, size: null });
                }}
              >
                查看
              </button>
            )}
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              aria-label="关闭封面导入提示"
              onClick={() => setCoverNotice(null)}
            >
              <Icon name="close" size="sm" />
            </button>
          </div>
        )}
      </Disclosure>
    </div>
  );
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
