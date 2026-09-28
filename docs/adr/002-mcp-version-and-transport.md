# ADR 002: MCP SDK and transport

- Status: accepted
- Date: 2026-09-22

## Decision

- Pin official Python SDK to `mcp[cli]==2.2.0`.
- Use local `stdio` transport for the MVP.
- Expose only the six required tools in the first release.

## Rationale

The official SDK v2 is the current stable major line and differs materially from v1. An exact dependency pin and the smallest local transport avoid API drift, authentication work, and network deployment complexity during the 19-day schedule.
