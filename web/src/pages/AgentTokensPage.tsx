import { useState } from "react";
import { useParams } from "react-router-dom";
import { useAgentTokens, useCreateAgentToken, useDeleteAgentToken } from "../hooks/useAgentTokens";
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
  const [copied, setCopied] = useState(false);
  const { mutate, isPending, error } = useCreateAgentToken(agentId);

  function handleCopy() {
    if (!created) return;
    navigator.clipboard.writeText(created.token).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    mutate(label.trim() || null, {
      onSuccess: (data) => setCreated(data),
    });
  }

  if (created) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
        <div className="w-full max-w-md rounded-lg bg-gray-900 p-6">
          <h2 className="mb-4 text-lg font-semibold text-white">Token created</h2>
          <p className="mb-2 text-sm font-bold text-yellow-400">
            Copy this token now. It will not be shown again.
          </p>
          <pre className="mb-4 whitespace-pre-wrap break-all rounded bg-gray-800 p-3 font-mono text-xs text-green-300">
            {created.token}
          </pre>
          <div className="flex justify-end gap-3">
            <button
              onClick={handleCopy}
              className="rounded px-4 py-2 text-sm text-white transition-colors hover:bg-gray-600 disabled:opacity-50 bg-gray-700"
              disabled={copied}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
            <button
              onClick={onClose}
              className="rounded bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-500"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg bg-gray-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">Create token</h2>
        <form onSubmit={handleSubmit}>
          <label className="mb-1 block text-sm text-gray-400" htmlFor="token-label">
            Label <span className="text-gray-600">(optional)</span>
          </label>
          <input
            id="token-label"
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="mb-4 w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
            placeholder="e.g. my-agent-device"
            disabled={isPending}
          />
          {error && (
            <p className="mb-4 text-sm text-red-400">{error.message}</p>
          )}
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
              className="rounded bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-cyan-300 disabled:opacity-50"
              disabled={isPending}
            >
              {isPending ? "Creating..." : "Create"}
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
  const { mutate: deleteToken, isPending: isDeleting, variables: deletingId } = useDeleteAgentToken(agentId!);

  if (isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Loading tokens...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Failed to load tokens. Please try again.</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {showCreate && (
        <CreateTokenDialog agentId={agentId!} onClose={() => setShowCreate(false)} />
      )}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-white">Tokens</h2>
          <p className="text-sm text-slate-400">
            Each agent device should get its own bearer token.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="whitespace-nowrap rounded-2xl bg-cyan-400 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300"
        >
          Create token
        </button>
      </div>

      <div className="rounded-[2rem] border border-white/10 bg-slate-900/80 p-4 sm:p-5">
        {tokens && tokens.length === 0 ? (
          <div className="rounded-[1.5rem] border border-dashed border-white/10 bg-white/[0.03] p-8 text-center">
            <p className="text-base font-medium text-white">No tokens found</p>
            <p className="mt-2 text-sm text-slate-400">
              Create a token when you are ready to connect a device or simulator.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2 sm:hidden">
              {tokens?.map((token) => (
                <div key={token.id} className="flex flex-col gap-2 rounded-lg bg-gray-900 p-4">
                  <div className="break-words text-sm text-white">
                    {token.label ?? <span className="italic text-gray-600">No label</span>}
                  </div>
                  <div className="text-xs text-gray-400">
                    {new Date(token.created_at).toLocaleString()}
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={() => deleteToken(token.id)}
                      disabled={isDeleting && deletingId === token.id}
                      className="text-sm text-red-400 transition-colors hover:text-red-300 disabled:opacity-50"
                    >
                      {isDeleting && deletingId === token.id ? "Deleting..." : "Delete"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div className="hidden overflow-hidden rounded-[1.5rem] border border-white/10 sm:block">
              <table className="w-full">
                <thead className="bg-white/[0.03]">
                  <tr className="text-left">
                    <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">Label</th>
                    <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">Created</th>
                    <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400"></th>
                  </tr>
                </thead>
                <tbody>
                  {tokens?.map((token) => (
                    <tr key={token.id} className="border-t border-gray-800">
                      <td className="px-4 py-3 text-sm text-white">
                        {token.label ?? <span className="italic text-gray-600">No label</span>}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-400">
                        {new Date(token.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right text-sm">
                        <button
                          onClick={() => deleteToken(token.id)}
                          disabled={isDeleting && deletingId === token.id}
                          className="text-red-400 transition-colors hover:text-red-300 disabled:opacity-50"
                        >
                          {isDeleting && deletingId === token.id ? "Deleting..." : "Delete"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
