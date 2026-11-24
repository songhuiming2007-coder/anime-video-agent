// 封面图导入（Spec 12 §2.1/§4.2）：紧凑拖放按钮 + 隐藏文件选择器，字节经 host spawn `/import-cover` 落 07-cover/import/。
// 人是唯一发起者：拖入 / 点选都是显式动作；host 对 data/ 零写入（I1 不变）。
import { useEffect, useRef, useState } from "react";
import type { ImportedCoverJson } from "../shared/protocol";
import { Icon } from "./icons";
import { errText, type RpcClient } from "./rpc";

export function CoverImport({
  epKey,
  rpc,
  onImported,
  onNotice,
}: {
  epKey: string;
  rpc: RpcClient;
  onImported: (r: ImportedCoverJson) => void;
  onNotice?: (msg: string | null) => void;
}) {
  const [dragover, setDragover] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    onNotice?.(null);
  }, [epKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const emitMsg = (next: string | null) => {
    onNotice?.(next);
  };

  const settle = async (call: Promise<ImportedCoverJson>): Promise<void> => {
    setBusy(true);
    emitMsg(null);
    try {
      const r = await call;
      emitMsg(`已导入 ${r.path}（${r.format} ${r.width}×${r.height}）`);
      onImported(r);
    } catch (e) {
      emitMsg(errText(e));
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
          setDragover(true);
        }}
        onDragLeave={() => setDragover(false)}
        onDrop={async (e) => {
          if (!e.nativeEvent.isTrusted) return;
          e.preventDefault();
          setDragover(false);
          const f = e.dataTransfer.files[0];
          if (!f) return;
          const bytes = new Uint8Array(await f.arrayBuffer());
          void settle(rpc.call("cover.import", { epKey, name: f.name, bytes }));
        }}
      >
        <button
          type="button"
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="cover-pick"
          disabled={busy}
          title="点击选择或直接拖入封面图（PNG/JPEG，≤ 32 MiB）"
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            inputRef.current?.click();
          }}
        >
          {busy ? <span className="ui-spinner" /> : <Icon name="image" size="sm" />}
          导入封面
        </button>
        <input
          ref={inputRef}
          className="hidden-input"
          type="file"
          accept="image/png,image/jpeg"
          data-testid="cover-file"
          onChange={async (e) => {
            if (!e.nativeEvent.isTrusted) return;
            const f = e.target.files?.[0];
            if (!f) return;
            e.target.value = "";
            const bytes = new Uint8Array(await f.arrayBuffer());
            void settle(rpc.call("cover.import", { epKey, name: f.name, bytes }));
          }}
        />
      </div>
    </div>
  );
}
