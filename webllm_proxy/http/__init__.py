"""Thin Flask blueprints: parse the request, call into `application`/
`providers/*/llmproxy`, shape the response with `wire`. One module per wire
protocol family (`openai_routes`, `anthropic_routes`), plus the cross-cutting
`health` helpers every route shares."""
