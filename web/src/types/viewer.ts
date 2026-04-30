export interface RfConfig {
  center_freq_hz: number;
  sample_rate_hz: number;
  fft_size: number;
  baseband_start_hz: number;
  baseband_end_hz: number;
  bin_size_hz: number;
  bin_count: number;
  window_fn: string;
}

export interface FftSemantics {
  kind: string;
  scale: string;
  unit: string;
  numeric_type: string;
  bin_order: string;
}

export interface ViewerSubscribeMessage {
  msg_type: "subscribe";
  agent_id: string;
}

export interface ViewerSubscribeAckMessage {
  msg_type: "subscribe_ack";
  agent_id: string;
  session_id: string;
  stream_id: string;
  status: "ok";
}

export interface ViewerStreamConfigMessage {
  msg_type: "stream_config";
  agent_id: string;
  session_id: string;
  stream_id: string;
  config_version: number;
  rf: RfConfig;
  fft_semantics: FftSemantics;
}

export interface ViewerSpectrumFrameMessage {
  msg_type: "spectrum_frame";
  agent_id: string;
  session_id: string;
  stream_id: string;
  config_version: number;
  frame_index: number;
  timestamp_utc: string;
  data: { payload: Float32Array };
}

export interface ViewerErrorMessage {
  msg_type: "error";
  code: string;
  message: string;
}

export type ViewerInboundMessage =
  | ViewerSubscribeAckMessage
  | ViewerStreamConfigMessage
  | ViewerSpectrumFrameMessage
  | ViewerErrorMessage;
