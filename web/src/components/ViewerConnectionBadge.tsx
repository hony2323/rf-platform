import type { ViewerConnectionState } from "../hooks/useViewerStream";

interface Props {
  state: ViewerConnectionState;
}

const STATE_STYLES: Record<
  ViewerConnectionState,
  { dot: string; label: string; bg: string; text: string }
> = {
  idle: {
    dot: "bg-gray-500",
    label: "Idle",
    bg: "bg-gray-800",
    text: "text-gray-400",
  },
  connecting: {
    dot: "bg-yellow-400 animate-pulse",
    label: "Connecting",
    bg: "bg-yellow-900",
    text: "text-yellow-300",
  },
  subscribed: {
    dot: "bg-blue-400",
    label: "Live",
    bg: "bg-blue-900",
    text: "text-blue-300",
  },
  offline: {
    dot: "bg-gray-500",
    label: "Agent offline",
    bg: "bg-gray-800",
    text: "text-gray-400",
  },
  error: {
    dot: "bg-red-400",
    label: "Error",
    bg: "bg-red-900",
    text: "text-red-300",
  },
};

export function ViewerConnectionBadge({ state }: Props) {
  const s = STATE_STYLES[state];
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full ${s.bg} ${s.text}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}
