# Security

AI Guild is a self-hosted, single-owner site. Its threat model:

- The site is behind a passphrase and a signed, host-bound, HttpOnly cookie. There is no account system and no reset flow; rotate secrets with `npm run secrets`.
- The collector authenticates with a separate bearer secret. A browser session can never write; the collector can never read.
- The Worker validates every uploaded field against an allowlist and scans payloads for secret-looking strings; anything unexpected is rejected.
- All responses are `private, no-store` with a strict Content-Security-Policy.

If you find a vulnerability, please open a private security advisory on GitHub (Security → Report a vulnerability) rather than a public issue. Describe the class of problem and a minimal reproduction; do not include real secrets.
