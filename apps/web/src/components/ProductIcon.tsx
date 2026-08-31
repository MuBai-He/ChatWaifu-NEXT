import {
  ArrowLeft,
  AudioLines,
  BookOpenText,
  Brain,
  Captions,
  Check,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleX,
  Database,
  History,
  type LucideProps,
  MessageSquareMore,
  Mic,
  MonitorCog,
  MoonStar,
  Network,
  PanelTopOpen,
  PlugZap,
  Plus,
  Puzzle,
  RefreshCw,
  RotateCcw,
  Send,
  Settings2,
  Square,
  Trash2,
  Volume2,
  X,
} from "lucide-react";

const productIcons = {
  alert: CircleAlert,
  back: ArrowLeft,
  captions: Captions,
  channel: MessageSquareMore,
  check: Check,
  connected: CircleCheck,
  controlCenter: Settings2,
  data: Database,
  disconnected: CircleX,
  display: PanelTopOpen,
  history: History,
  memory: Brain,
  microphone: Mic,
  models: Network,
  pet: MonitorCog,
  plugin: PlugZap,
  plus: Plus,
  pushToTalk: AudioLines,
  refresh: RefreshCw,
  reset: RotateCcw,
  send: Send,
  skills: Puzzle,
  stop: Square,
  story: BookOpenText,
  voice: Volume2,
  companion: MoonStar,
  continue: ChevronDown,
  close: X,
  trash: Trash2,
} as const;

export type ProductIconName = keyof typeof productIcons;

export interface ProductIconProps extends LucideProps {
  name: ProductIconName;
}

/**
 * The single functional-icon boundary shared by the web and desktop products.
 * Buttons retain their accessible name; decorative SVGs stay out of the
 * accessibility tree by default.
 */
export function ProductIcon({ name, ...props }: ProductIconProps) {
  const Icon = productIcons[name];
  return (
    <Icon aria-hidden="true" focusable="false" strokeWidth={1.8} {...props} />
  );
}
