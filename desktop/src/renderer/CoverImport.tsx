// 封面图导入（Spec 12 §2.1/§4.2）：拖放区 + 文件选择器，字节经 host spawn `/import-cover`（stdin）落 07-cover/import/。
// 人是唯一发起者：拖入 / 点选都是显式动作；host 对 data/ 零写入（I1 不变）。
// 压在图片上的文字只用 --overlay-*（Spec 14 S12-R1）。
import { useRef, useState } from "react";
import type { ImportedCoverJson } from "../shared/protocol";
import { Icon } from "./icons";
import { errText, type RpcClient } from "./rpc";

export function CoverImport({ epKey, rpc, onImported }: { epKey: string; rpc: RpcClient; onImported: (r: ImportedCoverJson) => void }) {
  const [dragover, setDragover] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const settle = async (call: Promise<ImportedCoverJson>): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await call;
      setMsg(`已导入 ${r.path}（${r.format} ${r.width}×${r.height}）`);
      onImported(r);
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cover-import">
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
        <Icon name="image" size="sm" />
        <span>拖入封面图（PNG/JPEG，≤ 32 MiB）</span>
        <button
          className="ui-btn ui-btn--sm"
          data-testid="cover-pick"
          disabled={busy}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            inputRef.current?.click();
          }}
        >
          选择文件…
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
      {msg !== null && (
        <div className="notice" data-testid="cover-import-msg">
          <Icon name="info" size="sm" />
          {msg}
        </div>
      )}
    </div>
  );
}
