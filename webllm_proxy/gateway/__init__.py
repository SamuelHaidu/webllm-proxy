"""Aggregator gateway: one OpenAI/Anthropic HTTP surface fronting every running
per-provider proxy. It merges each upstream's `/v1/models` (namespacing ids
`<provider>__<slug>`) and routes each request to the matching upstream by that
prefix. Pure forwarder -- no browser, no credentials of its own; bytes pass
through untouched and are never logged (secrets discipline). See
`app.build_gateway_app`."""
