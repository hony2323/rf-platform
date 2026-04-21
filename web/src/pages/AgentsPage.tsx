import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createAgent } from "../api/agents";
import { useAgents } from "../hooks/useAgents";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { AgentStatusBadge } from "../components/AgentStatusBadge";
import type { AgentResponse } from "../types/api";

function CreateAgentDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [nodeId, setNodeId] = useState("");
  const queryClient = useQueryClient();
  const { mutate, isPending, error } = useMutation({
    mutationFn: () => createAgent({ name: name.trim(), stable_node_id: nodeId.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onClose();
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim() && nodeId.trim()) mutate();
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-900 rounded-lg p-6 w-full max-w-md">
        <h2 className="text-white text-lg font-semibold mb-4">Create agent</h2>
        <form onSubmit={handleSubmit}>
          <label className="block text-gray-400 text-sm mb-1" htmlFor="agent-name">Name</label>
          <input
            id="agent-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 text-white text-sm rounded px-3 py-2 mb-3 focus:outline-none focus:border-blue-500"
            placeholder="e.g. Rooftop SDR"
            disabled={isPending}
          />
          <label className="block text-gray-400 text-sm mb-1" htmlFor="agent-node-id">
            Node ID <span className="text-gray-600">(stable identifier for this device)</span>
          </label>
          <input
            id="agent-node-id"
            type="text"
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 text-white text-sm rounded px-3 py-2 mb-4 focus:outline-none focus:border-blue-500"
            placeholder="e.g. node-abc123"
            disabled={isPending}
          />
          {error && <p className="text-red-400 text-sm mb-4">{error.message}</p>}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-400 hover:text-gray-300 text-sm transition-colors"
              disabled={isPending}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending || !name.trim() || !nodeId.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded transition-colors disabled:opacity-50"
            >
              {isPending ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

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
  const [showCreate, setShowCreate] = useState(false);
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
      {showCreate && <CreateAgentDialog onClose={() => setShowCreate(false)} />}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-white text-xl font-semibold">Agents</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded transition-colors"
        >
          Create agent
        </button>
      </div>

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
