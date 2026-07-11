"""`domain.ports.JobStore` implementations. `memory.MemoryJobStore` is the
default (and, today, the only one); swap in a persistent store by writing
another class with the same `put`/`get`/`list_jobs`/`delete` shape."""
