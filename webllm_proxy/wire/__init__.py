"""Pure wire-format helpers: turning values into (and out of) the JSON/SSE
shapes each client-facing API uses. No Flask, no browser, no provider
business logic -- everything here is a plain function, unit-testable without
either. One module per protocol family (`openai`, `anthropic`), shared by
whichever providers speak that protocol."""
