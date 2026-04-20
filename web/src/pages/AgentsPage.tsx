import { Link } from "react-router-dom";
import { useAgents } from "../hooks/useAgents";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { AgentStatusBadge } from "../components/AgentStatusBadge";
import type { AgentResponse } from "../types/api";

function AgentRow({ agent }: { agent: AgentResponse }) {
  const { data: status } = useAgentStatus(agent.id);

  return (
    <tr className="border-t border-gray-800">
      <td className="py-3 px-4 text-white text-sm">{agent.name}</td>
      <td className="py-3 px-4 text-gray-400 text-sm font-mono">{agent.stable_node_id}</td>
      <td className="py-3 px-4">
        {status ? (
          <AgentStatusBadge online={status.online} />
        ) : (
          <span className="text-gray-600 text-xs">—</span>
        )}
      </td>
      <td className="py-3 px-4 text-sm space-x-4">
        <Link
          to={`/agents/${agent.id}/live`}
          className="text-blue-400 hover:text-blue-300 transition-colors"
        >
          Live
        </Link>
        <Link
          to={`/agents/${agent.id}/tokens`}
          className="text-gray-400 hover:text-gray-300 transition-colors"
        >
          Tokens
        </Link>
      </td>
    </tr>
  );
}

export function AgentsPage() {
  const { data: agents, isLoading, error } = useAgents();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Loading agents…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Failed to load agents. Please try again.</span>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 p-8">
      <h1 className="text-white text-xl font-semibold mb-6">Agents</h1>

      {agents && agents.length === 0 ? (
        <p className="text-gray-400 text-sm">No agents found.</p>
      ) : (
        <div className="bg-gray-900 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="text-left">
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider">Name</th>
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider">Node ID</th>
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider">Status</th>
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {agents?.map((agent) => (
                <AgentRow key={agent.id} agent={agent} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
