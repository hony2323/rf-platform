import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAgent } from "../api/agents";
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
