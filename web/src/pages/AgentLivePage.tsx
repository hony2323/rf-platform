import { useState } from "react";
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
  const [dbfsFloor, setDbfsFloor] = useState(-120);
  const [dbfsCeiling, setDbfsCeiling] = useState(-50);

  const agentQuery = useQuery<AgentResponse, Error>({
    queryKey: ["agent", agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const statusQuery = useAgentStatus(agentId!);
  const { connectionState, config, lastError, onFrame } = useViewerStream(agentId!);

  if (agentQuery.error instanceof ApiError && agentQuery.error.status === 404) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Agent not found.</span>
      </div>
    );
  }

  if (agentQuery.isError) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Server error. Please try again.</span>
      </div>
    );
  }

  const agentName = agentQuery.data?.name ?? agentId ?? "Agent";
  const isOnline = statusQuery.data?.online ?? false;

  return (
    <div className="flex flex-col overflow-hidden rounded-[2rem] border border-white/10 bg-slate-900/80">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/10 bg-white/[0.03] px-3 py-3 sm:px-4">
        <Link
          to="/agents"
          className="text-sm text-gray-400 transition-colors hover:text-white"
        >
          Back to home
        </Link>
        <span className="hidden text-gray-600 sm:inline">|</span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-white sm:flex-none">
          {agentName}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <AgentStatusBadge online={isOnline} />
          <ViewerConnectionBadge state={connectionState} />
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-white/10 bg-white/[0.02] px-3 py-2 text-xs text-gray-500 sm:gap-6 sm:px-4">
        <span className="min-w-0 max-w-full truncate">
          <span className="mr-1 text-gray-600">session</span>
          {statusQuery.data?.session_id ?? "--"}
        </span>
        <span className="min-w-0 max-w-full truncate">
          <span className="mr-1 text-gray-600">heartbeat</span>
          {statusQuery.data?.last_heartbeat_at ?? "--"}
        </span>
        <span className="flex w-full items-center gap-1 sm:ml-auto sm:w-auto">
          <span className="text-gray-600">floor</span>
          <input
            type="number"
            inputMode="numeric"
            value={dbfsFloor}
            onChange={(e) => setDbfsFloor(Number(e.target.value))}
            className="w-16 rounded border border-gray-700 bg-gray-800 px-1 py-0.5 text-right text-xs text-gray-300 focus:border-blue-500 focus:outline-none"
          />
          <span className="text-gray-600">ceil</span>
          <input
            type="number"
            inputMode="numeric"
            value={dbfsCeiling}
            onChange={(e) => setDbfsCeiling(Number(e.target.value))}
            className="w-16 rounded border border-gray-700 bg-gray-800 px-1 py-0.5 text-right text-xs text-gray-300 focus:border-blue-500 focus:outline-none"
          />
          <span className="text-gray-600">dBFS</span>
        </span>
      </div>

      <main className="flex flex-1 flex-col">
        <WaterfallCanvas config={config} onFrame={onFrame} dbfsFloor={dbfsFloor} dbfsCeiling={dbfsCeiling} />

        {(connectionState === "error" || connectionState === "offline") && (
          <div className="flex items-center justify-center px-4 py-6">
            <div className="max-w-md rounded-lg border border-gray-700 bg-gray-900 px-6 py-4 text-center">
              <p className="text-sm font-medium text-gray-300">
                {connectionState === "offline"
                  ? "Agent is offline"
                  : "Connection error"}
              </p>
              {lastError && (
                <p className="mt-1 break-words text-xs text-gray-500">{lastError}</p>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
