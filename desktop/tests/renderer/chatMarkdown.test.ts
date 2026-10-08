// D46：助手回复的 Markdown 渲染器。放开 Spec 10 H-3 的前提（RF-13）：输出里不可能出现可点击控件或外部资源加载。
import { describe, expect, test } from "vitest";
import { renderChatMarkdown } from "../../src/renderer/PreviewPane";

const FORBIDDEN = ["<a", "<img", "<button", "<input", "<form", "<iframe", "<script", "<video", "<audio", "<object", "<embed", "<link", "<style"];
/** 真标签里的属性（转义后的 &lt;iframe src=… 是文字，不算） */
const ATTR_IN_TAG = /<[a-z][^>]*\s(href|src|on[a-z]+)=/;

const HOSTILE = [
  "[批准](https://evil.example/approve)",
  "[批准][r]\n\n[r]: https://evil.example/approve",
  "<https://evil.example/x>",
  "https://evil.example/bare",
  "![x](https://evil.example/pixel.png)",
  "![x][i]\n\n[i]: https://evil.example/pixel.png",
  "[x](javascript:alert(1))",
  '<button onclick="x()">批准</button>',
  '<a href="https://evil.example">点我</a>',
  '<img src="https://evil.example/p.png">',
  "<form><input></form>",
  "<script>alert(1)</script>",
  '<iframe src="https://evil.example"></iframe>',
  "| 列 |\n|---|\n| [x](https://evil.example) |",
  "- [x](https://evil.example)\n> ![y](https://evil.example/p.png)",
];

describe("D46 ChatMarkdown：不产出可点击控件、不加载资源", () => {
  for (const src of HOSTILE) {
    test(JSON.stringify(src), () => {
      const html = renderChatMarkdown(src).toLowerCase();
      for (const bad of FORBIDDEN) expect(html, `${bad} 出现在 ${html}`).not.toContain(bad);
      expect(html).not.toMatch(ATTR_IN_TAG);
    });
  }
});

describe("D46 ChatMarkdown：可读性元素照常渲染", () => {
  test("粗体、列表、分隔线、标题、表格、引用、行内代码", () => {
    const html = renderChatMarkdown("## 一\n\n**真实剧情**\n\n- 甲\n- 乙\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> 引\n\n`S01E05`");
    expect(html).toContain("<h2>一</h2>");
    expect(html).toContain("<strong>真实剧情</strong>");
    expect(html).toContain("<ul>\n<li>甲</li>");
    expect(html).toContain("<hr>");
    expect(html).toContain("<table>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<code>S01E05</code>");
  });
  test("链接写法按字面文字显示（不吞掉网址）", () => {
    expect(renderChatMarkdown("[批准](https://evil.example/approve)")).toBe("<p>[批准](https://evil.example/approve)</p>\n");
  });
});
