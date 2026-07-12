/** Talk to the aggregator gateway (`webllm-proxy gateway`). Kept tiny and
 *  side-effect-light so the provider factory stays declarative. */

import type { GatewayModel } from "./models";

/** Gateway base URL (no trailing slash). Override with WEBLLM_GATEWAY_URL. */
export function gatewayBaseUrl(): string {
  const raw = process.env.WEBLLM_GATEWAY_URL ?? "http://127.0.0.1:5100";
  return raw.replace(/\/+$/, "");
}

/** Fetch the merged/namespaced model list. Returns [] on any failure (gateway
 *  down, timeout, non-200) so startup degrades gracefully. */
export async function fetchGatewayModels(
  baseUrl: string,
  timeoutMs = 5000,
): Promise<GatewayModel[]> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${baseUrl}/v1/models`, { signal: controller.signal });
    if (!res.ok) return [];
    const body = (await res.json()) as { data?: GatewayModel[] };
    return Array.isArray(body.data) ? body.data : [];
  } catch {
    return [];
  } finally {
    clearTimeout(timer);
  }
}
