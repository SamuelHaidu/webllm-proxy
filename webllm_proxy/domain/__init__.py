"""The innermost layer: the ports (seams) every "workaround brick" plugs into,
plus the plain dataclasses passed across them. Imports nothing else from this
package -- everything else (providers, strategies, research backends, wire,
http, transport, infra, prompts) depends inward on `domain`, never the other
way around."""
