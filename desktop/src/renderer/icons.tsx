// Crisp optical UI icons backed by lucide-react (VS-10 pure presentational module).
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  AlertTriangle,
  ArrowUp,
  AudioWaveform,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Cpu,
  Eye,
  FileText,
  Film,
  Folder,
  FolderGit2,
  Gauge,
  Globe,
  Image,
  Info,
  Layers,
  Palette,
  PanelLeft,
  PanelRight,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  X,
} from "lucide-react";

const ICONS = {
  "chevron-right": ChevronRight,
  "chevron-down": ChevronDown,
  file: FileText,
  folder: Folder,
  film: Film,
  wave: AudioWaveform,
  image: Image,
  play: Play,
  pause: Pause,
  stop: Square,
  search: Search,
  refresh: RefreshCw,
  pulse: Activity,
  appearance: Palette,
  close: X,
  alert: AlertTriangle,
  info: Info,
  check: Check,
  terminal: Terminal,
  pen: Pencil,
  globe: Globe,
  stack: Layers,
  trash: Trash2,
  copy: Copy,
  repo: FolderGit2,
  gauge: Gauge,
  chip: Cpu,
  "panel-left": PanelLeft,
  "panel-right": PanelRight,
  eye: Eye,
  sliders: SlidersHorizontal,
  "arrow-up": ArrowUp,
  plus: Plus,
  sparkles: Sparkles,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

/** Render an optical icon sized to AVA's UI scale (15px default, 13px sm). */
export function Icon({ name, size, className }: { name: IconName; size?: "sm"; className?: string }) {
  const Component = ICONS[name];
  const px = size === "sm" ? 13 : 15;
  const cls = ["ui-icon", size === "sm" && "ui-icon--sm", className].filter(Boolean).join(" ");
  return <Component className={cls} size={px} strokeWidth={1.75} aria-hidden="true" />;
}
