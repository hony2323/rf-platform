export interface RfConfig {
  center_hz: number;
  sample_rate_hz: number;
  bin_count: number;
  fft_size: number;
  gain_db: number | null;
}

export interface FftSemantics {
  window: string;
  overlap: number;
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
  data: { payload: string };
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
