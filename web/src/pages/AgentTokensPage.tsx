import { useState } from "react";
import { useParams } from "react-router-dom";
import { useAgentTokens, useCreateAgentToken, useRevokeAgentToken } from "../hooks/useAgentTokens";
import type { TokenCreateResponse } from "../types/api";

function CreateTokenDialog({
  agentId,
  onClose,
}: {
  agentId: string;
  onClose: () => void;
}) {
  const [label, setLabel] = useState("");
  const [created, setCreated] = useState<TokenCreateResponse | null>(null);
  const { mutate, isPending, error } = useCreateAgentToken(agentId);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    mutate(label.trim() || null, {
      onSuccess: (data) => setCreated(data),
    });
  }

  if (created) {
    return (
      <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
        <div className="bg-gray-900 rounded-lg p-6 w-full max-w-md">
          <h2 className="text-white text-lg font-semibold mb-4">Token created</h2>
          <p className="text-gray-400 text-sm mb-2">
            Copy this token now — it will not be shown again.
          </p>
          <pre className="bg-gray-800 text-green-300 text-xs font-mono rounded p-3 break-all whitespace-pre-wrap mb-6">
            {created.token}
          </pre>
          <div className="flex justify-end">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-900 rounded-lg p-6 w-full max-w-md">
        <h2 className="text-white text-lg font-semibold mb-4">Create token</h2>
        <form onSubmit={handleSubmit}>
          <label className="block text-gray-400 text-sm mb-1" htmlFor="token-label">
            Label <span className="text-gray-600">(optional)</span>
          </label>
          <input
            id="token-label"
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 text-white text-sm rounded px-3 py-2 mb-4 focus:outline-none focus:border-blue-500"
            placeholder="e.g. my-agent-device"
            disabled={isPending}
          />
          {error && (
            <p className="text-red-400 text-sm mb-4">{error.message}</p>
          )}
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
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded transition-colors disabled:opacity-50"
              disabled={isPending}
            >
              {isPending ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function AgentTokensPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const [showCreate, setShowCreate] = useState(false);
  const { data: tokens, isLoading, error } = useAgentTokens(agentId!);
  const { mutate: revoke, isPending: isRevoking, variables: revokingId } = useRevokeAgentToken(agentId!);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Loading tokens…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Failed to load tokens. Please try again.</span>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 p-8">
      {showCreate && (
        <CreateTokenDialog agentId={agentId!} onClose={() => setShowCreate(false)} />
      )}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-white text-xl font-semibold">Tokens</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded transition-colors"
        >
          Create token
        </button>
      </div>

      {tokens && tokens.length === 0 ? (
        <p className="text-gray-400 text-sm">No tokens found.</p>
      ) : (
        <div className="bg-gray-900 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="text-left">
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider">Label</th>
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider">Created</th>
                <th className="py-3 px-4 text-gray-400 text-xs font-medium uppercase tracking-wider"></th>
              </tr>
            </thead>
            <tbody>
              {tokens?.map((token) => (
                <tr key={token.id} className="border-t border-gray-800">
                  <td className="py-3 px-4 text-white text-sm">
                    {token.label ?? <span className="text-gray-600 italic">No label</span>}
                  </td>
                  <td className="py-3 px-4 text-gray-400 text-sm">
                    {new Date(token.created_at).toLocaleString()}
                  </td>
                  <td className="py-3 px-4 text-sm text-right">
                    <button
                      onClick={() => revoke(token.id)}
                      disabled={isRevoking && revokingId === token.id}
                      className="text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
                    >
                      {isRevoking && revokingId === token.id ? "Revoking…" : "Revoke"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
