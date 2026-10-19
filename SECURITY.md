# Security

## Reporting a vulnerability

Email **vincent@q4o.com** with "Canot security" in the subject line. Do not open a public issue for a vulnerability.
Acknowledgment within 3 business days, fix or plan within 30 days.

## What Canot does with your data

- It runs in your accounts: the source is read in read-only mode, and the copy is written to storage you own.
- It sends nothing to q4o or to any third party: no telemetry, no server.
- Credentials are passed through environment variables or the provider CLI's local configuration, never on the command line.
- The key that signs the evidence log is created on the machine that runs the exercise (`~/.canot/evidence.key`, permissions 600).

## What the signature proves, and what it does not

It proves that the log has not been modified since it was signed (`canot verify-evidence`). It proves neither the author's identity nor the date. For dated evidence, store the evidence log in a timestamped archival system.

## Supported versions

The latest version published on PyPI. The project is in alpha.
