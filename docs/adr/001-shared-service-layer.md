# ADR 001: Shared application service layer

- Status: accepted
- Date: 2026-09-22

## Decision

HTTP API, CLI, and MCP are thin adapters over the same Python application services. The React GUI calls the HTTP API. No interface owns parsing, retrieval, question answering, claim extraction, conflict detection, citation validation, caching, or feedback rules.

## Consequences

- One behavior and test surface serves all interfaces.
- MCP tool tests can replace infrastructure dependencies with fakes.
- Interface-specific validation is allowed, but domain decisions stay in services.
