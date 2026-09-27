// TD-3 编辑器状态机（Spec 11 §4.2，纯 reducer 半边）：保存成功更新基线；冲突不丢本地内容；
// dirty-seal 规则（红队 🔴-2）：dirty 时「封板」与「从草稿新建」禁用。
import { describe, expect, it } from "vitest";
import { canCreateFromDraft, canSeal, emptyEditor, expectOf, reduceEditor, type ScriptEditorState } from "../../src/shared/editorState";

const FP = { size: "1200", mtimeNs: "1790171112636927676" };

function loaded(text = "正文"): ScriptEditorState {
  return reduceEditor(emptyEditor, { type: "loaded", text, baselineFp: FP });
}

describe("TD-3 编辑器状态机", () => {
  it("装载：脏标记与冲突清零，基线为磁盘指纹", () => {
    const s = loaded();
    expect(s).toEqual({ baselineFp: FP, text: "正文", dirty: false, conflict: false, saving: false });
  });

  it("编辑置 dirty；保存成功后基线更新、dirty/conflict 清除", () => {
    let s = loaded();
    s = reduceEditor(s, { type: "edit", text: "正文改" });
    expect(s.dirty).toBe(true);
    expect(canSeal(s)).toBe(false);
    s = reduceEditor(s, { type: "saving" });
    s = reduceEditor(s, { type: "saved", baselineFp: { size: "1260", mtimeNs: "1790171112636927000" } });
    expect(s.dirty).toBe(false);
    expect(s.conflict).toBe(false);
    expect(s.baselineFp).toEqual({ size: "1260", mtimeNs: "1790171112636927000" });
    expect(canSeal(s)).toBe(true);
  });

  it("冲突：conflict 置位、本地内容不丢（MUT-14 的纯函数半边）", () => {
    let s = loaded();
    s = reduceEditor(s, { type: "edit", text: "我的修改" });
    s = reduceEditor(s, { type: "conflict" });
    expect(s.conflict).toBe(true);
    expect(s.text).toBe("我的修改");
    expect(s.dirty).toBe(true);
    expect(s.saving).toBe(false);
  });

  it("重新载入：丢弃本地内容、回到干净基线", () => {
    let s = loaded();
    s = reduceEditor(s, { type: "edit", text: "我的修改" });
    s = reduceEditor(s, { type: "reload", text: "磁盘版本", baselineFp: { size: "9", mtimeNs: "1" } });
    expect(s).toEqual({ baselineFp: { size: "9", mtimeNs: "1" }, text: "磁盘版本", dirty: false, conflict: false, saving: false });
  });

  it("dirty 时「封板」与「从草稿新建」都禁用（红队 🔴-2 / MUT-16）", () => {
    const clean = loaded();
    expect(canSeal(clean)).toBe(true);
    const dirty = reduceEditor(clean, { type: "edit", text: "x" });
    expect(canSeal(dirty)).toBe(false);
    expect(canCreateFromDraft(dirty, true)).toBe(false);
  });

  it("从草稿新建：仅当正稿缺席 + 草稿在 + 不脏时可用", () => {
    const draftNew = reduceEditor(emptyEditor, { type: "loaded", text: "草稿正文", baselineFp: null });
    expect(canCreateFromDraft(draftNew, true)).toBe(true);
    expect(canCreateFromDraft(draftNew, false)).toBe(false); // 无草稿
    expect(canSeal(draftNew)).toBe(false); // 正稿不存在，无可封板对象
    expect(canCreateFromDraft(reduceEditor(draftNew, { type: "edit", text: "y" }), true)).toBe(false);
  });

  it("expectOf：正稿在时给指纹；缺席时给 -1/-1（断言文件不存在）", () => {
    expect(expectOf(loaded())).toEqual({ expectSize: "1200", expectMtimeNs: "1790171112636927676" });
    const draftNew = reduceEditor(emptyEditor, { type: "loaded", text: "", baselineFp: null });
    expect(expectOf(draftNew)).toEqual({ expectSize: "-1", expectMtimeNs: "-1" });
  });
});
