read_when: you found a security issue, need to disclose a vulnerability, or want to understand the project's security reporting expectations

# Security Policy

## Supported reporting path

Do not open public GitHub issues for suspected security vulnerabilities until a
maintainer confirms public disclosure is appropriate.

Use a private maintainer contact path when available on the hosting platform. If
no private advisory workflow is configured yet, contact the maintainer directly
through the private contact route listed on the repository host.

## What to include

- A clear description of the issue.
- The affected version or commit.
- Reproduction steps or proof of concept.
- The security impact you believe it has.
- Any suggested mitigation if you have one.

## Response expectations

This is a personal project, not a staffed security team. Reports will be triaged
in good faith, but there is no guaranteed SLA.

## Scope notes

Likely relevant classes of issues here include:

- secret handling or unintended secret disclosure
- sandbox or approval bypass
- trust-boundary violations
- command execution or file access that exceeds the documented contract
- malformed session state or RPC paths that can be abused across boundaries
