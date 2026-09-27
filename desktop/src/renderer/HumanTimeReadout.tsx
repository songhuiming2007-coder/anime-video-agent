// 期视图头部的人时读数（Spec 11 §2.4）：host 直读 human_time.json，按停机点分布呈现。
// 「含终端与桌面记录，走神照算，不作门禁」——人时是观测量，没有任何门禁语义。
import { useEffect, useState } from "react";
import type { TimeReadJson } from "../shared/protocol";
import type { RpcClient } from "./rpc";

const STOPS = ["02.5", "03.5", "05", "09"] as const;

export function HumanTimeReadout({ epKey, rpc, version }: { epKey: string; rpc: RpcClient; version: number }) {
  const [r, setR] = useState<TimeReadJson | null>(null);
  useEffect(() => {
    let live = true;
    rpc.call<TimeReadJson>("time.read", { epKey }).then(
      (v) => {
        if (live) setR(v);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [epKey, rpc, version]);
  if (r === null) return null;
  const parts = STOPS.map((s) => `${s} ${r.perStop[s].toFixed(1)} m`);
  return (
    <div className="notice" data-testid="human-time">
      人时（墙钟·观测量）：{parts.join(" · ")} · 其他 {r.otherMin.toFixed(1)} m · 合计 {r.totalMin.toFixed(1)} m。
      含终端与桌面记录，走神照算，不作门禁。
    </div>
  );
}
