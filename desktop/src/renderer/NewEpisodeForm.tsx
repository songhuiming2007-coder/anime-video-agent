// 建期表单（Spec 10 §2.5；S8-R2 的 episode.create）。全仓唯一出现 "episode.create" 的字面量的文件。
// 期名初值恒为空串字面量、不从对话内容预填（TG-15，direction §5）；校验与建目录全在 core（C10-R1）。
// 该字面量的调用点只在「建期」按钮的 onClick 里（TG-10）。
import { useState } from "react";
import type { CreatedEpisode } from "../shared/protocol";
import type { RpcClient } from "./rpc";
import { errText } from "./rpc";

export function NewEpisodeForm({ rpc, onCreated }: { rpc: RpcClient; onCreated: (created: CreatedEpisode) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="new-episode" data-testid="new-episode">
      <button className="ui-btn ui-btn--sm" data-testid="new-episode-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        ＋ 新建一期
      </button>
      {open && (
        <div className="new-episode-form">
          <label className="ui-field">
            期名
            <input className="ui-input" data-testid="episode-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如 2026-10-01-春物-…" />
          </label>
          <button
            className="ui-btn"
            data-testid="episode-create"
            disabled={busy || name.trim() === ""}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              setBusy(true);
              setProblem(null);
              rpc.call<CreatedEpisode>("episode.create", { name }).then(
                (r) => {
                  setBusy(false);
                  setName("");
                  setOpen(false);
                  onCreated(r);
                },
                (err) => {
                  setBusy(false);
                  setProblem(errText(err)); // core 的原文（stderr 尾部）原样显示
                },
              );
            }}
          >
            建期
          </button>
          {busy && <span className="muted">建期中…</span>}
          {problem && (
            <div className="error" data-testid="new-episode-error">
              {problem}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
