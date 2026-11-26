import { isValidElement, type ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { stripClosingAttrs } from "../../src/renderer/motion";

describe("stripClosingAttrs", () => {
  it("剥离顶层与深层嵌套的 data-testid 与 role，并保持数组子元素原有 key（不触发退场卸载重挂）", () => {
    const items = [
      <span key="k1" data-testid="row-1">
        甲
      </span>,
      <span key="k2">乙</span>,
    ];
    const tree = (
      <div data-testid="banner" role="alert">
        <span>前缀</span>
        <button type="button" data-testid="banner-close">
          关闭
        </button>
        <div>{items}</div>
      </div>
    );

    const stripped = stripClosingAttrs(tree) as ReactElement<{
      "data-testid"?: string;
      role?: string;
      children: [ReactElement, ReactElement<{ "data-testid"?: string }>, ReactElement<{ children: ReactElement<{ "data-testid"?: string }>[] }>];
    }>;

    expect(isValidElement(stripped)).toBe(true);
    expect(stripped.props["data-testid"]).toBeUndefined();
    expect(stripped.props.role).toBeUndefined();

    const [prefix, closeBtn, listBox] = stripped.props.children;
    // 无 key 静态数组子元素必须保持 key === null（Children.map 会错改成 ".0"/".1"）
    expect(prefix.key).toBeNull();
    expect(closeBtn.key).toBeNull();
    expect(listBox.key).toBeNull();
    expect(closeBtn.props["data-testid"]).toBeUndefined();

    // 带 key 的列表子元素必须保持原字符串 key（Children.map 会错改成 ".$k1"/"$k2"）
    const [r1, r2] = listBox.props.children;
    expect(r1.key).toBe("k1");
    expect(r2.key).toBe("k2");
    expect(r1.props["data-testid"]).toBeUndefined();
    // 无需剥离的节点原样复用引用
    expect(r2).toBe(items[1]);
  });

  it("整棵树不含 data-testid 与 role 时原样返回同一引用", () => {
    const plain = (
      <div>
        <span>纯文本</span>
      </div>
    );
    expect(stripClosingAttrs(plain)).toBe(plain);
  });
});
