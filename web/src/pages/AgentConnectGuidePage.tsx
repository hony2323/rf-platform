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
    <div className="relative group">
      <pre className="bg-gray-950 border border-gray-800 text-green-300 text-xs sm:text-sm font-mono rounded p-3 overflow-x-auto whitespace-pre">
        {children}
      </pre>
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 px-2 py-1 text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 rounded transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
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
      <h2 className="text-white text-base sm:text-lg font-semibold mb-3">
        <span className="inline-block w-7 h-7 rounded-full bg-blue-600 text-white text-sm text-center leading-7 mr-2">
          {n}
        </span>
        {title}
      </h2>
      <div className="pl-9 text-sm text-gray-300 space-y-3">{children}</div>
    </section>
  );
}

export function AgentConnectGuidePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent, isLoading, error } = useAgent(agentId!);
  const [mode, setMode] = useState<Mode>("file");

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Loading agent…</span>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">
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
    `  --token <PASTE_TOKEN_HERE> \\`,
    `  --file ./${SIGMF_META_FILENAME} \\`,
    `  --fps 15`,
  ].join("\n");

  return (
    <div className="min-h-screen bg-gray-950 p-4 sm:p-8">
      <div className="max-w-3xl mx-auto">
        <div className="mb-6">
          <Link
            to="/agents"
            className="text-gray-500 hover:text-gray-300 text-sm transition-colors"
          >
            &larr; Back to agents
          </Link>
        </div>

        <header className="mb-8">
          <h1 className="text-white text-xl sm:text-2xl font-semibold mb-2">
            Connect your agent
          </h1>
          <p className="text-gray-400 text-sm">
            Agent{" "}
            <span className="text-white font-medium">{agent.name}</span> created.
            Follow the steps below to stream spectrum data from a device or a
            recorded file.
          </p>
          <div className="mt-3 text-xs text-gray-500 font-mono">
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
              className="text-blue-400 hover:text-blue-300 underline"
            >
              Tokens page
            </Link>{" "}
            and copy it — it is only shown once.
          </p>
        </Step>

        <Step n={3} title="Choose a source">
          <div className="flex gap-2 mb-3">
            <button
              onClick={() => setMode("file")}
              className={`px-4 py-2 text-sm rounded transition-colors ${
                mode === "file"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-300 hover:bg-gray-700"
              }`}
            >
              File (sample recording)
            </button>
            <button
              onClick={() => setMode("sdr")}
              className={`px-4 py-2 text-sm rounded transition-colors ${
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
              <ul className="list-disc list-inside space-y-1">
                <li>
                  <a
                    href={SIGMF_DATA_PATH}
                    download={SIGMF_DATA_FILENAME}
                    className="text-blue-400 hover:text-blue-300 underline font-mono text-xs sm:text-sm break-all"
                  >
                    {SIGMF_DATA_FILENAME}
                  </a>{" "}
                  <span className="text-gray-500 text-xs">(IQ samples)</span>
                </li>
                <li>
                  <a
                    href={SIGMF_META_PATH}
                    download={SIGMF_META_FILENAME}
                    className="text-blue-400 hover:text-blue-300 underline font-mono text-xs sm:text-sm break-all"
                  >
                    {SIGMF_META_FILENAME}
                  </a>{" "}
                  <span className="text-gray-500 text-xs">
                    (metadata — required)
                  </span>
                </li>
              </ul>
              <p>
                Then run the agent from that directory (replace{" "}
                <code className="text-gray-200 bg-gray-800 px-1 py-0.5 rounded text-xs">
                  &lt;PASTE_TOKEN_HERE&gt;
                </code>{" "}
                with your token):
              </p>
              <CodeBlock>{fileCommand}</CodeBlock>
              <p className="text-xs text-gray-500">
                Centre frequency and sample rate are read automatically from the
                <code className="text-gray-300 mx-1">.sigmf-meta</code> file.
              </p>
            </>
          ) : (
            <div className="bg-gray-900 border border-gray-800 rounded p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="px-2 py-0.5 text-xs rounded bg-yellow-900/40 text-yellow-300 border border-yellow-900">
                  Coming soon
                </span>
                <span className="text-white text-sm font-medium">
                  Real SDR hardware
                </span>
              </div>
              <p className="text-sm text-gray-400 mb-3">
                Support for RTL-SDR and other USB/network SDR devices is on the
                way. The concept:
              </p>
              <ul className="list-disc list-inside text-sm text-gray-400 space-y-1">
                <li>
                  Install the SDR extra:{" "}
                  <code className="text-gray-200 bg-gray-950 px-1 py-0.5 rounded text-xs">
                    pip install "rf-agent[sdr]"
                  </code>
                </li>
                <li>Plug in your SDR device (RTL-SDR, HackRF, USRP, …)</li>
                <li>
                  Run{" "}
                  <code className="text-gray-200 bg-gray-950 px-1 py-0.5 rounded text-xs">
                    rf-agent connect
                  </code>{" "}
                  pointing at the device instead of a file — centre frequency,
                  sample rate, and gain will be configurable from the server.
                </li>
              </ul>
            </div>
          )}
        </Step>

        <Step n={4} title="Watch it live">
          <p>
            Once the agent is running you should see{" "}
            <code className="text-green-300 bg-gray-800 px-1 py-0.5 rounded text-xs">
              [rf-agent] connected
            </code>{" "}
            in its terminal. Open the{" "}
            <Link
              to={`/agents/${agent.id}/live`}
              className="text-blue-400 hover:text-blue-300 underline"
            >
              Live view
            </Link>{" "}
            to see the spectrum stream.
          </p>
        </Step>
      </div>
    </div>
  );
}
