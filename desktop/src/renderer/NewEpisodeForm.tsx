// 建期表单（Spec 10 §2.5；S8-R2 的 episode.create）。全仓唯一出现 "episode.create" 的字面量的文件。
// 期名初值恒为空串字面量、不从对话内容预填（TG-15，direction §5）；校验与建目录全在 core（C10-R1）。
// 该字面量的调用点只在「建期」按钮的 onClick / 输入框的 onKeyDown 里（TG-10）。
import { useEffect, useRef, useState } from "react";
import type { CreatedEpisode } from "../shared/protocol";
import { FloatingPopover } from "./floating";
import { Icon } from "./icons";
import { Disclosure } from "./motion";
import type { RpcClient } from "./rpc";
import { errText } from "./rpc";

function todayPlaceholder(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `例如 ${y}-${m}-${day}-番名-…`;
}

export function NewEpisodeForm({
  rpc,
  onCreated,
  asPopover = false,
  defaultFromIdea = false,
  hasIdeaSession = false,
}: {
  rpc: RpcClient;
  onCreated: (created: CreatedEpisode) => void;
  asPopover?: boolean;
  defaultFromIdea?: boolean;
  hasIdeaSession?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [bringIdea, setBringIdea] = useState(defaultFromIdea);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [errSeq, setErrSeq] = useState(0);
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) {
      setProblem(null);
      setBringIdea(defaultFromIdea);
    } else {
      setProblem(null);
    }
  }, [open, defaultFromIdea]);

  const closeForm = (returnFocus = false) => {
    setOpen(false);
    setProblem(null);
    if (returnFocus) requestAnimationFrame(() => toggleRef.current?.focus());
  };

  const formBody = (
    <div className="new-episode-form">
      <label className="ui-field">
        期名
        <input
          ref={inputRef}
          className="ui-input"
          data-testid="episode-name"
          autoFocus
          disabled={busy}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (problem) setProblem(null);
          }}
          onKeyDown={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            if (e.key === "Escape") {
              e.preventDefault();
              closeForm(true);
              return;
            }
            if (e.key !== "Enter" || e.nativeEvent.isComposing || e.keyCode === 229 || busy || name.trim() === "") return;
            e.preventDefault();
            setBusy(true);
            setProblem(null);
            rpc
              .call<CreatedEpisode>("episode.create", {
                name: name.trim(),
                fromIdea: hasIdeaSession && bringIdea ? "true" : "false",
              })
              .then(
                (r) => {
                  setBusy(false);
                  setName("");
                  setOpen(false);
                  onCreated(r);
                },
                (err) => {
                  setBusy(false);
                  setProblem(errText(err));
                  setErrSeq((n) => n + 1);
                },
              );
          }}
          placeholder={todayPlaceholder()}
        />
      </label>
      {hasIdeaSession && (
        <label className="new-episode-check">
          <input
            type="checkbox"
            data-testid="episode-from-idea"
            checked={bringIdea}
            disabled={busy}
            onChange={(e) => setBringIdea(e.target.checked)}
          />
          <span>带入当前选题讨论</span>
        </label>
      )}
      <div className="new-episode-actions">
        <button
          type="button"
          className="ui-btn ui-btn--primary ui-btn--sm"
          data-testid="episode-create"
          disabled={busy || name.trim() === ""}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            setBusy(true);
            setProblem(null);
            rpc
              .call<CreatedEpisode>("episode.create", {
                name: name.trim(),
                fromIdea: hasIdeaSession && bringIdea ? "true" : "false",
              })
              .then(
                (r) => {
                  setBusy(false);
                  setName("");
                  setOpen(false);
                  onCreated(r);
                },
                (err) => {
                  setBusy(false);
                  setProblem(errText(err));
                  setErrSeq((n) => n + 1);
                },
              );
          }}
        >
          {busy && <span className="ui-spinner" />}
          {busy ? "建期中…" : "建期"}
        </button>
        <button
          type="button"
          className="ui-btn ui-btn--ghost ui-btn--sm"
          disabled={busy}
          onClick={() => closeForm(true)}
        >
          取消
        </button>
      </div>
      <Disclosure open={problem !== null} keepLastContent>
        {problem && (
          <div key={errSeq} className="error ui-error-shake" data-testid="new-episode-error">
            {problem}
          </div>
        )}
      </Disclosure>
    </div>
  );

  if (asPopover) {
    return (
      <div className="new-episode new-episode--popover" data-testid="new-episode">
        <FloatingPopover
          open={open}
          onOpenChange={setOpen}
          placement="bottom-end"
          role="dialog"
          initialFocus={inputRef}
          className="ui-popover new-episode-pop"
          renderTrigger={(trigger) => (
            <button
              type="button"
              ref={(node) => {
                trigger.ref(node);
                toggleRef.current = node;
              }}
              {...trigger.props}
              className="ui-btn ui-btn--ghost ui-btn--sm"
              data-testid="new-episode-toggle"
              aria-expanded={open}
            >
              <Icon name="plus" size="sm" />
              新建一期
            </button>
          )}
          renderContent={() => (
            <>
              <div className="ui-popover-title">新建一期</div>
              {formBody}
            </>
          )}
        />
      </div>
    );
  }

  return (
    <div className="new-episode" data-testid="new-episode">
      <button
        type="button"
        ref={toggleRef}
        className="ui-row proj-new"
        data-testid="new-episode-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Icon name="plus" size="sm" />
        <span className="ui-row-title">新建一期</span>
      </button>
      <Disclosure open={open}>{formBody}</Disclosure>
    </div>
  );
}

// ---------------- D81：建企划、在企划下建卷（同一文件：TG-10 建期类方法只在本文件的点击/按键处理器里） ----------------
// 企划名 / 卷名初值同样是空串字面量、不从对话预填（TG-15 的同一纪律）；目标企划是宿主扫出来的对象（不是可预填的名字）。

export function NewProjectForm({ rpc, onCreated }: { rpc: RpcClient; onCreated: (projKey: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [errSeq, setErrSeq] = useState(0);
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const closeForm = (returnFocus = false) => {
    setOpen(false);
    setProblem(null);
    if (returnFocus) requestAnimationFrame(() => toggleRef.current?.focus());
  };
  return (
    <div className="new-project" data-testid="new-project">
      <button
        type="button"
        ref={toggleRef}
        className="ui-row proj-new"
        data-testid="new-project-toggle"
        onClick={() => {
          setProblem(null);
          setOpen((v) => !v);
        }}
        aria-expanded={open}
      >
        <Icon name="plus" size="sm" />
        <span className="ui-row-title">新建企划</span>
      </button>
      <Disclosure open={open}>
        <div className="new-episode-form">
          <label className="ui-field">
            企划名
            <input
              className="ui-input"
              data-testid="project-name"
              autoFocus
              disabled={busy}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (problem) setProblem(null);
              }}
              onKeyDown={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                if (e.key === "Escape") {
                  e.preventDefault();
                  closeForm(true);
                  return;
                }
                if (e.key !== "Enter" || e.nativeEvent.isComposing || e.keyCode === 229 || busy || name.trim() === "") return;
                e.preventDefault();
                setBusy(true);
                setProblem(null);
                rpc.call<{ projKey: string }>("project.create", { name: name.trim() }).then(
                  (r) => {
                    setBusy(false);
                    setName("");
                    setOpen(false);
                    onCreated(r.projKey);
                  },
                  (err) => {
                    setBusy(false);
                    setProblem(errText(err));
                    setErrSeq((n) => n + 1);
                  },
                );
              }}
              placeholder="例如 supercell-编年史"
            />
          </label>
          <div className="new-episode-actions">
            <button
              type="button"
              className="ui-btn ui-btn--primary ui-btn--sm"
              data-testid="project-create"
              disabled={busy || name.trim() === ""}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                setBusy(true);
                setProblem(null);
                rpc.call<{ projKey: string }>("project.create", { name: name.trim() }).then(
                  (r) => {
                    setBusy(false);
                    setName("");
                    setOpen(false);
                    onCreated(r.projKey);
                  },
                  (err) => {
                    setBusy(false);
                    setProblem(errText(err));
                    setErrSeq((n) => n + 1);
                  },
                );
              }}
            >
              {busy && <span className="ui-spinner" />}
              {busy ? "建企划中…" : "建企划"}
            </button>
            <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" disabled={busy} onClick={() => closeForm(true)}>
              取消
            </button>
          </div>
          <Disclosure open={problem !== null} keepLastContent>
            {problem && (
              <div key={errSeq} className="error ui-error-shake" data-testid="new-project-error">
                {problem}
              </div>
            )}
          </Disclosure>
        </div>
      </Disclosure>
    </div>
  );
}

export function NewVolumeForm({ rpc, target, onCreated }: { rpc: RpcClient; target: { projKey: string }; onCreated: (created: CreatedEpisode) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [errSeq, setErrSeq] = useState(0);
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const closeForm = (returnFocus = false) => {
    setOpen(false);
    setProblem(null);
    if (returnFocus) requestAnimationFrame(() => toggleRef.current?.focus());
  };
  return (
    <div className="new-volume" data-testid="new-volume">
      <button
        type="button"
        ref={toggleRef}
        className="ui-row proj-new"
        data-testid="new-volume-toggle"
        onClick={() => {
          setProblem(null);
          setOpen((v) => !v);
        }}
        aria-expanded={open}
      >
        <Icon name="plus" size="sm" />
        <span className="ui-row-title">新建卷</span>
      </button>
      <Disclosure open={open}>
        <div className="new-episode-form">
          <label className="ui-field">
            卷名
            <input
              className="ui-input"
              data-testid="volume-name"
              autoFocus
              disabled={busy}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (problem) setProblem(null);
              }}
              onKeyDown={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                if (e.key === "Escape") {
                  e.preventDefault();
                  closeForm(true);
                  return;
                }
                if (e.key !== "Enter" || e.nativeEvent.isComposing || e.keyCode === 229 || busy || name.trim() === "") return;
                e.preventDefault();
                setBusy(true);
                setProblem(null);
                rpc.call<CreatedEpisode>("episode.create", { name: name.trim(), project: target.projKey }).then(
                  (r) => {
                    setBusy(false);
                    setName("");
                    setOpen(false);
                    onCreated(r);
                  },
                  (err) => {
                    setBusy(false);
                    setProblem(errText(err));
                    setErrSeq((n) => n + 1);
                  },
                );
              }}
              placeholder="例如 01-…"
            />
          </label>
          <div className="new-episode-actions">
            <button
              type="button"
              className="ui-btn ui-btn--primary ui-btn--sm"
              data-testid="volume-create"
              disabled={busy || name.trim() === ""}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                setBusy(true);
                setProblem(null);
                rpc.call<CreatedEpisode>("episode.create", { name: name.trim(), project: target.projKey }).then(
                  (r) => {
                    setBusy(false);
                    setName("");
                    setOpen(false);
                    onCreated(r);
                  },
                  (err) => {
                    setBusy(false);
                    setProblem(errText(err));
                    setErrSeq((n) => n + 1);
                  },
                );
              }}
            >
              {busy && <span className="ui-spinner" />}
              {busy ? "建卷中…" : "建卷"}
            </button>
            <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" disabled={busy} onClick={() => closeForm(true)}>
              取消
            </button>
          </div>
          <Disclosure open={problem !== null} keepLastContent>
            {problem && (
              <div key={errSeq} className="error ui-error-shake" data-testid="new-volume-error">
                {problem}
              </div>
            )}
          </Disclosure>
        </div>
      </Disclosure>
    </div>
  );
}
