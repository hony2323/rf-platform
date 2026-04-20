export function decodeFloat32Payload(
  base64Payload: string,
  expectedBinCount: number,
): Float32Array | null {
  let binary: string;
  try {
    binary = atob(base64Payload);
  } catch {
    return null;
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  if (bytes.byteLength !== expectedBinCount * 4) {
    if (import.meta.env.DEV) {
      console.error(
        `decodeFloat32Payload: expected ${expectedBinCount * 4} bytes, got ${bytes.byteLength}`,
      );
    }
    return null;
  }
  return new Float32Array(bytes.buffer);
}
