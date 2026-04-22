# rf-agent

SDR agent for live RF spectrum streaming. Reads IQ samples from an SDR device (or a file/simulator), runs FFT, and streams spectrum frames over WebSocket to an rf-platform server.

## Install

```
pip install rf-agent
```

With RTL-SDR hardware support:

```
pip install "rf-agent[sdr]"
```

## Usage

```
rf-agent connect --server ws://your-server/ws/agent --token <token>
```

See `rf-agent connect --help` for all options.
