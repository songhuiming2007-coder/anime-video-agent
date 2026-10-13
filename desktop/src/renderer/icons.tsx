// 仓库内手绘 24 个 SVG（Spec 14 §2.5）：16px 网格、1.5 描边、currentColor、无颜色字面量。
// D80 照同一规格补 6 个：工具类别（终端 / 写入 / 网页 / 流水线）、悬停复制、会话行的删除。
// D91 再补 2 个：模型（chip）、推理强度（gauge）。
// 纯展示件：无事件属性、带 aria-hidden（VS-10）；不引任何图标库。
// 路径数据由附件 tools/icons.mjs 原样移植（Spec 14 §2.7 交付物形态）。

const ICONS = {
  "chevron-right": <path d="M6 3.5 10.5 8 6 12.5" />,
  "chevron-down": <path d="M3.5 6 8 10.5 12.5 6" />,
  file: (
    <>
      <path d="M4 1.75h5.25L12.5 5v9.25H4z" />
      <path d="M9 1.75V5.25h3.5" />
    </>
  ),
  folder: <path d="M1.75 3.75h4.5l1.5 1.5h6.5v7.5H1.75z" />,
  film: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="M5 3v10M11 3v10M2 6.5h3M2 9.5h3M11 6.5h3M11 9.5h3" />
    </>
  ),
  wave: <path d="M2 8h1.5M4.75 5.5v5M7.25 3v10M9.75 5v6M12.25 7v2" />,
  image: (
    <>
      <rect x="2" y="2.75" width="12" height="10.5" rx="1.5" />
      <circle cx="5.75" cy="6.25" r="1.25" />
      <path d="m2.5 12 3.5-3.5 2.5 2.5 2-2 3 3" />
    </>
  ),
  play: <path d="M5 3.25v9.5L12.5 8z" />,
  stop: <rect x="4" y="4" width="8" height="8" rx="1" />,
  search: (
    <>
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3.5 3.5" />
    </>
  ),
  refresh: (
    <>
      <path d="M13.25 8A5.25 5.25 0 1 1 11.7 4.3" />
      <path d="M13.25 2.5v3.25H10" />
    </>
  ),
  pulse: <path d="M1.5 8.5h3l1.75-4 3.25 7.5 1.75-3.5h3.25" />,
  appearance: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 2.25v11.5" />
      <path d="M8 3.5a4.5 4.5 0 0 1 0 9z" fill="currentColor" stroke="none" />
    </>
  ),
  close: <path d="m4 4 8 8M12 4l-8 8" />,
  alert: (
    <>
      <path d="M8 2.25 14.25 13.25H1.75z" />
      <path d="M8 6.5v3" />
      <circle cx="8" cy="11.25" r="0.5" fill="currentColor" />
    </>
  ),
  info: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 7.25v4" />
      <circle cx="8" cy="5" r="0.5" fill="currentColor" />
    </>
  ),
  check: <path d="m3.5 8.5 3 3 6-7" />,
  terminal: (
    <>
      <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" />
      <path d="m4.5 6 2 2-2 2M8.5 10.5h3" />
    </>
  ),
  pen: <path d="m10.5 2.5 3 3-8 8h-3v-3z" />,
  globe: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M2.25 8h11.5M8 2.25c2 2.1 2 9.4 0 11.5M8 2.25c-2 2.1-2 9.4 0 11.5" />
    </>
  ),
  stack: <path d="M2.25 4.5h11.5M2.25 8h11.5M2.25 11.5h11.5" />,
  trash: (
    <>
      <path d="M2.75 4.25h10.5M6.25 4.25V2.75h3.5v1.5" />
      <path d="M4 4.25l.75 9h6.5l.75-9M6.75 6.75v4M9.25 6.75v4" />
    </>
  ),
  copy: (
    <>
      <rect x="5.25" y="5.25" width="8.5" height="8.5" rx="1.5" />
      <path d="M10.75 5.25v-2a1 1 0 0 0-1-1h-6.5a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h2" />
    </>
  ),
  repo: (
    <>
      <path d="M3 2.25h8.5a1 1 0 0 1 1 1v10.5H4a1 1 0 0 1-1-1z" />
      <path d="M3 11.75h9.5" />
    </>
  ),
  // D91：推理强度（仪表盘）。ZCode 用大脑，但 16px / 1.5 描边下大脑只剩一团（2026-10-14 实渲对照，见问题单 D91）
  gauge: (
    <>
      <path d="M2.75 11.25a5.25 5.25 0 1 1 10.5 0" />
      <path d="M8 11.25 10.75 7" />
      <path d="M2.75 11.25h1.5M11.75 11.25h1.5M8 6V4.75" />
    </>
  ),
  // D91：模型（芯片）
  chip: (
    <>
      <rect x="4" y="4" width="8" height="8" rx="1.5" />
      <path d="M6.5 1.75V4M9.5 1.75V4M6.5 12v2.25M9.5 12v2.25M1.75 6.5H4M1.75 9.5H4M12 6.5h2.25M12 9.5h2.25" />
    </>
  ),
};

export type IconName = keyof typeof ICONS;

export function Icon({ name, size }: { name: IconName; size?: "sm" }) {
  return (
    <svg
      className={size === "sm" ? "ui-icon ui-icon--sm" : "ui-icon"}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name]}
    </svg>
  );
}
