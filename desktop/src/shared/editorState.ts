// 02.5 编辑器状态机（Spec 11 §2.2/§4.2，纯 reducer、零 DOM）。
// 指纹一律十进制字符串（基准 = 磁盘版本；bigint 只在 host 内流通，Spec 8 §3.1 规则 9）。
// dirty-seal 规则（红队 🔴-2，冻结）：dirty 时「封板」与「从草稿新建」禁用——
// 封板对象必须是磁盘上人已确认的版本，屏幕上看到的与被封板的不许是两版。

export interface BaselineFp {
  size: string;
  mtimeNs: string;
}

export interface ScriptEditorState {
  /** 磁盘基线指纹；null = `02-script.md` 不存在（「从草稿新建」形态） */
  baselineFp: BaselineFp | null;
  text: string;
  /** 与磁盘版本相比未保存的改动 */
  dirty: boolean;
  /** 上一次保存被 core 拒（指纹不符）；本地内容不丢 */
  conflict: boolean;
  saving: boolean;
}

export type EditorAction =
  | { type: "loaded"; text: string; baselineFp: BaselineFp | null }
  | { type: "edit"; text: string }
  | { type: "saving" }
  | { type: "saved"; baselineFp: BaselineFp }
  | { type: "conflict" }
  | { type: "reload"; text: string; baselineFp: BaselineFp | null };

export const emptyEditor: ScriptEditorState = { baselineFp: null, text: "", dirty: false, conflict: false, saving: false };

export function reduceEditor(s: ScriptEditorState, a: EditorAction): ScriptEditorState {
  switch (a.type) {
    case "loaded":
    case "reload":
      return { baselineFp: a.baselineFp, text: a.text, dirty: false, conflict: false, saving: false };
    case "edit":
      return a.text === s.text ? s : { ...s, text: a.text, dirty: true };
    case "saving":
      return { ...s, saving: true };
    case "saved":
      // 保存成功：新基线、脏标记与冲突清除
      return { ...s, baselineFp: a.baselineFp, dirty: false, conflict: false, saving: false };
    case "conflict":
      return { ...s, saving: false, conflict: true };
  }
}

/** 「封板」可用：磁盘版本存在且缓冲区无未保存改动。 */
export function canSeal(s: ScriptEditorState): boolean {
  return !s.saving && !s.dirty && s.baselineFp !== null;
}

/** 「从草稿新建」可用：`02-script.md` 缺席、草稿在、且无未保存改动（同一 dirty-seal 哲学）。 */
export function canCreateFromDraft(s: ScriptEditorState, draftExists: boolean): boolean {
  return !s.saving && !s.dirty && s.baselineFp === null && draftExists;
}

/** 保存时携带的期望指纹（`-1/-1` 断言文件不存在，Spec 11 §3.1）。 */
export function expectOf(s: ScriptEditorState): { expectSize: string; expectMtimeNs: string } {
  return s.baselineFp === null ? { expectSize: "-1", expectMtimeNs: "-1" } : { expectSize: s.baselineFp.size, expectMtimeNs: s.baselineFp.mtimeNs };
}

/**
 * D49-A S3：磁盘指纹与编辑器基线比对后该做什么（纯函数）。
 * - `same`：一致，或正在保存（保存回来会自己更新基线），或磁盘上没有定稿（删除不自动处理）；
 * - `reload`：磁盘变了且没有未保存改动 → 自动载入新版本（agent 改稿后预览跟着变）；
 * - `warn`：磁盘变了但人有未保存改动 → 只提示，绝不覆盖人的改动。
 */
export function diskVerdict(s: ScriptEditorState, disk: BaselineFp | null): "same" | "reload" | "warn" {
  if (s.saving || disk === null) return "same";
  const base = s.baselineFp;
  if (base !== null && base.size === disk.size && base.mtimeNs === disk.mtimeNs) return "same";
  return s.dirty ? "warn" : "reload";
}
