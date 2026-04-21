import { wsUrl } from "../config/api";

export function viewerWsUrl(): string {
  return wsUrl("/ws/viewer");
}
