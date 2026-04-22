import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createAgent } from "../api/agents";
import { useAgents } from "../hooks/useAgents";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { AgentStatusBadge } from "../components/AgentStatusBadge";
import type { AgentResponse } from "../types/api";

function CreateAgentDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [nodeId, setNodeId] = useState("");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { mutate, isPending, error } = useMutation({
    mutationFn: () => createAgent({ name: name.trim(), stable_node_id: nodeId.trim() }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      onClose();
      void navigate(`/agents/${created.id}/connect`);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim() && nodeId.trim()) mutate();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-[1.75rem] border border-white/10 bg-slate-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">Create agent</h2>
        <form onSubmit={handleSubmit}>
          <label className="mb-1 block text-sm text-gray-400" htmlFor="agent-name">Name</label>
          <input
            id="agent-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mb-3 w-full rounded-2xl border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-cyan-400 focus:outline-none"
            placeholder="e.g. Rooftop SDR"
            disabled={isPending}
          />
          <label className="mb-1 block text-sm text-gray-400" htmlFor="agent-node-id">
            Node ID <span className="text-gray-600">(stable identifier for this device)</span>
          </label>
          <input
            id="agent-node-id"
            type="text"
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            className="mb-4 w-full rounded-2xl border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-cyan-400 focus:outline-none"
            placeholder="e.g. node-abc123"
            disabled={isPending}
          />
          {error && <p className="mb-4 text-sm text-red-400">{error.message}</p>}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-400 transition-colors hover:text-gray-300"
              disabled={isPending}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending || !name.trim() || !nodeId.trim()}
              className="rounded-2xl bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-50"
            >
              {isPending ? "Creating..." : "Create"}
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
      <td className="px-4 py-3 text-sm text-white">{agent.name}</td>
      <td className="px-4 py-3 font-mono text-sm text-gray-400">{agent.stable_node_id}</td>
      <td className="px-4 py-3">
        {status ? (
          <AgentStatusBadge online={status.online} />
        ) : (
          <span className="text-xs text-gray-600">--</span>
        )}
      </td>
      <td className="space-x-4 px-4 py-3 text-sm">
        <Link
          to={`/agents/${agent.id}/live`}
          className="text-blue-400 transition-colors hover:text-blue-300"
        >
          Live
        </Link>
        <Link
          to={`/agents/${agent.id}/connect`}
          className="text-gray-400 transition-colors hover:text-gray-300"
        >
          Connect
        </Link>
        <Link
          to={`/agents/${agent.id}/tokens`}
          className="text-gray-400 transition-colors hover:text-gray-300"
        >
          Tokens
        </Link>
      </td>
    </tr>
  );
}

function AgentCard({ agent }: { agent: AgentResponse }) {
  const { data: status } = useAgentStatus(agent.id);

  return (
    <div className="flex flex-col gap-2 rounded-[1.5rem] border border-white/10 bg-slate-900/80 p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 break-words text-sm font-medium text-white">{agent.name}</span>
        {status ? (
          <AgentStatusBadge online={status.online} />
        ) : (
          <span className="text-xs text-gray-600">--</span>
        )}
      </div>
      <div className="break-all font-mono text-xs text-gray-400">{agent.stable_node_id}</div>
      <div className="flex gap-4 pt-1 text-sm">
        <Link
          to={`/agents/${agent.id}/live`}
          className="text-blue-400 transition-colors hover:text-blue-300"
        >
          Live
        </Link>
        <Link
          to={`/agents/${agent.id}/connect`}
          className="text-gray-400 transition-colors hover:text-gray-300"
        >
          Connect
        </Link>
        <Link
          to={`/agents/${agent.id}/tokens`}
          className="text-gray-400 transition-colors hover:text-gray-300"
        >
          Tokens
        </Link>
      </div>
    </div>
  );
}

export function AgentsPage() {
  const [showCreate, setShowCreate] = useState(false);
  const { data: agents, isLoading, error } = useAgents();
  const { data: currentUser } = useCurrentUser();

  if (isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Loading agents...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Failed to load agents. Please try again.</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {showCreate && <CreateAgentDialog onClose={() => setShowCreate(false)} />}

      <section className="grid gap-4 lg:grid-cols-[1.35fr_0.65fr]">
        <div className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
          <p className="text-xs uppercase tracking-[0.28em] text-cyan-300/80">
            Dashboard
          </p>
          <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-3">
              <h2 className="text-2xl font-semibold text-white">
                {currentUser?.email ?? "Your workspace"}
              </h2>
              <p className="max-w-2xl text-sm text-slate-400">
                Create agents, open live views, and keep token management close.
                Keep your SDR fleet and live sessions organized in one place.
              </p>
            </div>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center justify-center rounded-2xl bg-cyan-400 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300"
            >
              Create agent
            </button>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-1">
          <div className="rounded-[1.5rem] border border-white/10 bg-slate-900/80 p-5">
            <p className="text-xs uppercase tracking-[0.24em] text-slate-500">
              Agents
            </p>
            <p className="mt-3 text-3xl font-semibold text-white">
              {agents?.length ?? 0}
            </p>
          </div>
          <div className="rounded-[1.5rem] border border-cyan-400/20 bg-cyan-400/10 p-5">
            <p className="text-xs uppercase tracking-[0.24em] text-cyan-100/80">
              Access
            </p>
            <p className="mt-3 text-sm text-cyan-50/90">
              Use Home in the top bar to return here from any protected page.
            </p>
          </div>
          <div className="rounded-[1.5rem] border border-amber-400/20 bg-amber-400/10 p-5">
            <p className="text-xs uppercase tracking-[0.24em] text-amber-200/80">
              Quick Start
            </p>
            <p className="mt-3 text-sm text-amber-50/90">
              Create an agent, mint a token, then open the live view.
            </p>
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.3fr_0.7fr]">
        <div className="rounded-[2rem] border border-white/10 bg-slate-900/80 p-4 sm:p-5">
          <div className="mb-5">
            <h3 className="text-lg font-semibold text-white">Agents</h3>
            <p className="text-sm text-slate-400">
              Open live streams, connection instructions, and token management.
            </p>
          </div>

          {agents && agents.length === 0 ? (
            <div className="rounded-[1.5rem] border border-dashed border-white/10 bg-white/[0.03] p-8 text-center">
              <p className="text-base font-medium text-white">No agents yet</p>
              <p className="mt-2 text-sm text-slate-400">
                Start by creating your first agent. You will get a connect guide
                immediately after creation.
              </p>
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-2 sm:hidden">
                {agents?.map((agent) => (
                  <AgentCard key={agent.id} agent={agent} />
                ))}
              </div>
              <div className="hidden overflow-hidden rounded-[1.5rem] border border-white/10 sm:block">
                <table className="w-full">
                  <thead className="bg-white/[0.03]">
                    <tr className="text-left">
                      <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">Name</th>
                      <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">Node ID</th>
                      <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">Status</th>
                      <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agents?.map((agent) => (
                      <AgentRow key={agent.id} agent={agent} />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <aside className="space-y-4">
          <div className="rounded-[1.5rem] border border-white/10 bg-white/5 p-5">
            <h3 className="text-base font-semibold text-white">Suggested next steps</h3>
            <div className="mt-4 space-y-3 text-sm text-slate-400">
              <p>1. Create an agent for each SDR device or recording source.</p>
              <p>2. Generate a token only when you are ready to connect a device.</p>
              <p>3. Open the live page to verify the first frames are arriving.</p>
            </div>
          </div>
        </aside>
      </section>
    </div>
  );
}
