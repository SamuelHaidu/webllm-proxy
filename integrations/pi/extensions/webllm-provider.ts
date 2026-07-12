/**
 * Registers webllm-proxy as a single `webllm` pi provider, fronted by the
 * aggregator gateway (`webllm-proxy gateway`, default :5100).
 *
 * Async factory: it discovers models from the gateway's `/v1/models` before
 * startup finishes, so they show up in `pi --list-models` and interactive model
 * selection. If the gateway is unreachable it registers nothing rather than
 * failing pi startup, and stays quiet by default so a global install does not
 * warn on every session (it only warns when WEBLLM_GATEWAY_URL is set).
 *
 * Model ids stay namespaced `<provider>__<slug>`; the gateway routes each
 * request back to the right per-provider proxy by that prefix.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { fetchGatewayModels, gatewayBaseUrl } from "../src/gateway";
import { mapModels } from "../src/models";

export default async function webllmProvider(pi: ExtensionAPI): Promise<void> {
  const base = gatewayBaseUrl();
  const models = mapModels(await fetchGatewayModels(base));

  if (models.length === 0) {
    // Quiet by default (e.g. gateway not running) so a global install does not
    // spam every pi session; warn only when the user explicitly opted in by
    // pointing us at a gateway via WEBLLM_GATEWAY_URL.
    if (process.env.WEBLLM_GATEWAY_URL) {
      console.error(
        `[webllm] no models from gateway at ${base} — is 'webllm-proxy gateway' running with at least one provider served?`,
      );
    }
    return;
  }

  pi.registerProvider("webllm", {
    name: "WebLLM Proxy",
    baseUrl: `${base}/v1`,
    // The gateway forwards to local proxies that ignore auth; a literal keeps
    // pi's openai-completions client happy without needing a real key.
    apiKey: "webllm-local",
    api: "openai-completions",
    models,
  });
}
