import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAgent } from "../api/agents";
import { ApiError } from "../api/client";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { useViewerStream } from "../hooks/useViewerStream";
import { AgentStatusBadge } from "../components/AgentStatusBadge";
import { ViewerConnectionBadge } from "../components/ViewerConnectionBadge";
import { WaterfallCanvas } from "../components/WaterfallCanvas";
import type { AgentResponse } from "../types/api";

export function AgentLivePage() {
  const { agentId } = useParams<{ agentId: string }>();

  const agentQuery = useQuery<AgentResponse, Error>({
    queryKey: ["agent", agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const statusQuery = useAgentStatus(agentId!);
  const { connectionState, config, lastError, onFrame } = useViewerStream(agentId!);

  if (agentQuery.error instanceof ApiError && agentQuery.error.status === 404) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Agent not found.</span>
      </div>
    );
  }

  if (agentQuery.isError) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Server error. Please try again.</span>
      </div>
    );
  }

  const agentName = agentQuery.data?.name ?? agentId ?? "Agent";
  const isOnline = statusQuery.data?.online ?? false;

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col">
      {/* Top bar */}
      <header className="flex items-center gap-3 px-4 py-3 bg-gray-900 border-b border-gray-800">
        <Link
          to="/agents"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          ← Agents
        </Link>
        <span className="text-gray-600">|</span>
        <span className="text-white font-medium text-sm">{agentName}</span>
        <AgentStatusBadge online={isOnline} />
        <ViewerConnectionBadge state={connectionState} />
      </header>

      {/* Session info bar */}
      <div className="flex items-center gap-6 px-4 py-2 bg-gray-900 border-b border-gray-800 text-xs text-gray-500">
        <span>
          <span className="text-gray-600 mr-1">session</span>
          {statusQuery.data?.session_id ?? "—"}
        </span>
        <span>
          <span className="text-gray-600 mr-1">heartbeat</span>
          {statusQuery.data?.last_heartbeat_at ?? "—"}
        </span>
      </div>

      {/* Waterfall */}
      <main className="flex-1 flex flex-col">
        <WaterfallCanvas config={config} onFrame={onFrame} />

        {/* Error / offline panel */}
        {(connectionState === "error" || connectionState === "offline") && (
          <div className="flex items-center justify-center py-6">
            <div className="bg-gray-900 border border-gray-700 rounded-lg px-6 py-4 text-center max-w-md">
              <p className="text-gray-300 text-sm font-medium">
                {connectionState === "offline"
                  ? "Agent is offline"
                  : "Connection error"}
              </p>
              {lastError && (
                <p className="text-gray-500 text-xs mt-1">{lastError}</p>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
