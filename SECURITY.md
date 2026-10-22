# Security and engineering safety

This is a reference implementation, not a hardened public service or certified engineering assessment system.

- Do not expose the examples as an unauthenticated network service.
- Treat reports, retrieved content, model output and remembered steps as untrusted inputs. Apply file size, archive expansion, request, process and time budgets in your deployment.
- Model callbacks are explicit. Confirm data-transfer authority before sending engineering records to a provider.
- Public state storage requires an explicit path or `ENGINEERING_AGENT_STATE_DB`. Tracing uses `ENGINEERING_AGENT_TRACE_DIR`; traces may contain input and output, so apply redaction and retention controls.
- The bounded arithmetic interpreter is not an operating-system sandbox. Registered Python functions and lookup providers are trusted application code.
- A verified field is not a verified site condition. Human review and applicable engineering procedures remain necessary.
- SQLite checkpoints and internal rollback cannot undo external actions. Add separate authorization, idempotency and compensation protocols.

Please do not post real credentials, engineering archives, personal information or exploit payloads containing private data in a public issue. If the repository's private vulnerability-reporting channel is available, use it; otherwise open a minimal issue asking for a private contact route without disclosing the exploit. No response-time guarantee is implied.

Release scanning checks known patterns and allowlisted file types; a clean scan is not a proof of absence of secrets or vulnerabilities.
