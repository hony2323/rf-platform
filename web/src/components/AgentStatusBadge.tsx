interface Props {
  online: boolean;
}

export function AgentStatusBadge({ online }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full ${
        online
          ? "bg-green-900 text-green-300"
          : "bg-gray-800 text-gray-400"
      }`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          online ? "bg-green-400" : "bg-gray-500"
        }`}
      />
      {online ? "Online" : "Offline"}
    </span>
  );
}
