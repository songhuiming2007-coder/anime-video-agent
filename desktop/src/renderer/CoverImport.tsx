// 封面图导入（Spec 12 §2.1/§4.2）：紧凑拖放按钮 + 隐藏文件选择器，字节经 host spawn `/import-cover` 落 07-cover/import/。
// 人是唯一发起者：拖入 / 点选都是显式动作；host 对 data/ 零写入（I1 不变）。
import { useEffect, useRef, useState } from "react";
import type { ImportedCoverJson } from "../shared/protocol";
import { FloatingTooltip } from "./floating";
import { Icon } from "./icons";
import { errText, type RpcClient } from "./rpc";

const COVER_MAX_BYTES = 32 * 1024 * 1024;
const ALLOWED_EXT = /\.(png|jpe?g)$/i;
const ALLOWED_MIME = new Set(["image/png", "image/jpeg", ""]);

export interface CoverNotice {
  kind: "ok" | "err";
  text: string;
  path?: string;
}

function validateCoverFile(f: File): string | null {
  if (!ALLOWED_EXT.test(f.name) || !ALLOWED_MIME.has(f.type)) {
    return "仅支持 PNG 或 JPEG 格式的封面图";
  }
  if (f.size <= 0) {
    return "所选图片文件为空";
  }
  if (f.size > COVER_MAX_BYTES) {
    return "图片超过 32 MiB 上限，请压缩后再导入";
  }
  return null;
}

export function CoverImport({
  epKey,
  rpc,
  onImported,
  onNotice,
}: {
  epKey: string;
  rpc: RpcClient;
  onImported: (r: ImportedCoverJson) => void;
  onNotice?: (notice: CoverNotice | null) => void;
}) {
  const [dragover, setDragover] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    onNotice?.(null);
  }, [epKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const emit = (next: CoverNotice | null) => {
    onNotice?.(next);
  };

  const settle = async (call: Promise<ImportedCoverJson>): Promise<void> => {
    setBusy(true);
    emit(null);
    try {
      const r = await call;
      emit({ kind: "ok", text: `已导入 ${r.path}（${r.format} ${r.width}×${r.height}）`, path: r.path });
      onImported(r);
    } catch (e) {
      emit({ kind: "err", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cover-compact">
      <div
        className="ui-dropzone"
        data-testid="cover-dropzone"
        data-dragover={dragover ? "true" : undefined}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragover(true);
        }}
        onDragLeave={(e) => {
          if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
          setDragover(false);
        }}
        onDrop={async (e) => {
          if (!e.nativeEvent.isTrusted) return;
          e.preventDefault();
          setDragover(false);
          if (busy) return;
          const f = e.dataTransfer.files[0];
          if (!f) return;
          const bad = validateCoverFile(f);
          if (bad) {
            emit({ kind: "err", text: bad });
            return;
          }
          try {
            const bytes = new Uint8Array(await f.arrayBuffer());
            void settle(rpc.call("cover.import", { epKey, name: f.name, bytes }));
          } catch (err) {
            emit({ kind: "err", text: `读取文件失败：${errText(err)}` });
          }
        }}
      >
        <FloatingTooltip
          label="点击选择或直接拖入封面图（PNG / JPEG，≤ 32 MiB）"
          renderTrigger={(t) => (
            <button
              type="button"
              ref={t.ref}
              {...t.props}
              className="ui-btn ui-btn--ghost ui-btn--sm"
              data-testid="cover-pick"
              disabled={busy}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                inputRef.current?.click();
              }}
            >
              {busy ? <span className="ui-spinner" /> : <Icon name="image" size="sm" />}
              {busy ? "导入中…" : "导入封面"}
            </button>
          )}
        />
        <input
          ref={inputRef}
          className="hidden-input"
          type="file"
          accept="image/png,image/jpeg"
          data-testid="cover-file"
          onChange={async (e) => {
            if (!e.nativeEvent.isTrusted) return;
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f || busy) return;
            const bad = validateCoverFile(f);
            if (bad) {
              emit({ kind: "err", text: bad });
              return;
            }
            try {
              const bytes = new Uint8Array(await f.arrayBuffer());
              void settle(rpc.call("cover.import", { epKey, name: f.name, bytes }));
            } catch (err) {
              emit({ kind: "err", text: `读取文件失败：${errText(err)}` });
            }
          }}
        />
      </div>
    </div>
  );
}
