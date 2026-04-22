import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAgent } from "../hooks/useAgents";

const SIGMF_DATA_PATH = "/sample/LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-data";
const SIGMF_META_PATH = "/sample/LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-meta";
const SIGMF_DATA_FILENAME = "LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-data";
const SIGMF_META_FILENAME = "LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-meta";

type Mode = "file" | "sdr";

function wsAgentUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/agent`;
}

function CodeBlock({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(children).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="group relative">
      <pre className="overflow-x-auto whitespace-pre rounded border border-gray-800 bg-gray-950 p-3 font-mono text-xs text-green-300 sm:text-sm">
        {children}
      </pre>
      <button
        onClick={handleCopy}
        className="absolute right-2 top-2 rounded bg-gray-800 px-2 py-1 text-xs text-gray-300 opacity-0 transition-colors group-hover:opacity-100 hover:bg-gray-700 focus:opacity-100"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6">
      <h2 className="mb-3 text-base font-semibold text-white sm:text-lg">
        <span className="mr-2 inline-block h-7 w-7 rounded-full bg-blue-600 text-center text-sm leading-7 text-white">
          {n}
        </span>
        {title}
      </h2>
      <div className="space-y-3 pl-9 text-sm text-gray-300">{children}</div>
    </section>
  );
}

export function AgentConnectGuidePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent, isLoading, error } = useAgent(agentId!);
  const [mode, setMode] = useState<Mode>("file");

  if (isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">Loading agent...</span>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="text-sm text-gray-400">
          Failed to load agent. Please try again.
        </span>
      </div>
    );
  }

  const serverUrl = wsAgentUrl();
  const fileCommand = [
    "rf-agent connect \\",
    `  --server ${serverUrl} \\`,
    `  --node-id ${agent.stable_node_id} \\`,
    "  --token <PASTE_TOKEN_HERE> \\",
    `  --file ./${SIGMF_META_FILENAME} \\`,
    "  --fps 15",
  ].join("\n");

  return (
    <div className="mx-auto max-w-3xl rounded-[2rem] border border-white/10 bg-slate-900/80 p-4 sm:p-8">
      <div className="mb-6">
        <Link
          to="/agents"
          className="text-sm text-gray-500 transition-colors hover:text-gray-300"
        >
          &larr; Back to agents
        </Link>
      </div>

      <header className="mb-8">
        <h1 className="mb-2 text-xl font-semibold text-white sm:text-2xl">
          Connect your agent
        </h1>
        <p className="text-sm text-gray-400">
          Agent{" "}
          <span className="font-medium text-white">{agent.name}</span> created.
          Follow the steps below to stream spectrum data from a device or a
          recorded file.
        </p>
        <div className="mt-3 font-mono text-xs text-gray-500">
          node_id: <span className="text-gray-300">{agent.stable_node_id}</span>
        </div>
      </header>

      <Step n={1} title="Install the rf-agent CLI">
        <p>Requires Python 3.10 or newer.</p>
        <CodeBlock>pip install rf-agent</CodeBlock>
      </Step>

      <Step n={2} title="Create an access token">
        <p>
          Each agent device needs a bearer token to authenticate with the
          server. Create one on the{" "}
          <Link
            to={`/agents/${agent.id}/tokens`}
            className="text-blue-400 underline hover:text-blue-300"
          >
            Tokens page
          </Link>{" "}
          and copy it. It is only shown once.
        </p>
      </Step>

      <Step n={3} title="Choose a source">
        <div className="mb-3 flex gap-2">
          <button
            onClick={() => setMode("file")}
            className={`rounded px-4 py-2 text-sm transition-colors ${
              mode === "file"
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            }`}
          >
            File (sample recording)
          </button>
          <button
            onClick={() => setMode("sdr")}
            className={`rounded px-4 py-2 text-sm transition-colors ${
              mode === "sdr"
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            }`}
          >
            Real SDR
          </button>
        </div>

        {mode === "file" ? (
          <>
            <p>
              Download this sample SigMF recording (LTE uplink at 847 MHz, 30.72
              Msps). Save both files into the same directory.
            </p>
            <ul className="list-inside list-disc space-y-1">
              <li>
                <a
                  href={SIGMF_DATA_PATH}
                  download={SIGMF_DATA_FILENAME}
                  className="break-all font-mono text-xs text-blue-400 underline hover:text-blue-300 sm:text-sm"
                >
                  {SIGMF_DATA_FILENAME}
                </a>{" "}
                <span className="text-xs text-gray-500">(IQ samples)</span>
              </li>
              <li>
                <a
                  href={SIGMF_META_PATH}
                  download={SIGMF_META_FILENAME}
                  className="break-all font-mono text-xs text-blue-400 underline hover:text-blue-300 sm:text-sm"
                >
                  {SIGMF_META_FILENAME}
                </a>{" "}
                <span className="text-xs text-gray-500">(metadata, required)</span>
              </li>
            </ul>
            <p>
              Then run the agent from that directory (replace{" "}
              <code className="rounded bg-gray-800 px-1 py-0.5 text-xs text-gray-200">
                &lt;PASTE_TOKEN_HERE&gt;
              </code>{" "}
              with your token):
            </p>
            <CodeBlock>{fileCommand}</CodeBlock>
            <p className="text-xs text-gray-500">
              Centre frequency and sample rate are read automatically from the
              <code className="mx-1 text-gray-300">.sigmf-meta</code> file.
            </p>
          </>
        ) : (
          <div className="rounded border border-gray-800 bg-gray-900 p-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded border border-yellow-900 bg-yellow-900/40 px-2 py-0.5 text-xs text-yellow-300">
                Coming soon
              </span>
              <span className="text-sm font-medium text-white">
                Real SDR hardware
              </span>
            </div>
            <p className="mb-3 text-sm text-gray-400">
              Support for RTL-SDR and other USB or network SDR devices is on the
              way. The concept:
            </p>
            <ul className="list-inside list-disc space-y-1 text-sm text-gray-400">
              <li>
                Install the SDR extra:{" "}
                <code className="rounded bg-gray-950 px-1 py-0.5 text-xs text-gray-200">
                  pip install "rf-agent[sdr]"
                </code>
              </li>
              <li>Plug in your SDR device (RTL-SDR, HackRF, USRP, and similar).</li>
              <li>
                Run{" "}
                <code className="rounded bg-gray-950 px-1 py-0.5 text-xs text-gray-200">
                  rf-agent connect
                </code>{" "}
                pointing at the device instead of a file. Centre frequency,
                sample rate, and gain will be configurable from the server.
              </li>
            </ul>
          </div>
        )}
      </Step>

      <Step n={4} title="Watch it live">
        <p>
          Once the agent is running you should see{" "}
          <code className="rounded bg-gray-800 px-1 py-0.5 text-xs text-green-300">
            [rf-agent] connected
          </code>{" "}
          in its terminal. Open the{" "}
          <Link
            to={`/agents/${agent.id}/live`}
            className="text-blue-400 underline hover:text-blue-300"
          >
            Live view
          </Link>{" "}
          to see the spectrum stream.
        </p>
      </Step>
    </div>
  );
}
