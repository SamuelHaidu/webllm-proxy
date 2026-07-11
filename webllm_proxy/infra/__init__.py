"""Cross-cutting infrastructure shared by every provider: env/config helpers,
logging + debug-dump setup, and secret redaction. Nothing here depends on
`domain`/`application`/providers -- it sits at the bottom of the dependency
graph, alongside `transport`."""
