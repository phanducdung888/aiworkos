"""HTTP API layer. Thin by contract: parse, authenticate, delegate, serialize.

Empty in this checkpoint. Routers arrive only after the application services they delegate to exist,
so that there is never a moment when a router is the only place a rule lives.
"""
