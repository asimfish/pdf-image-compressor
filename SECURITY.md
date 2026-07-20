# Security Policy

## Supported versions

Security fixes are applied to the latest published release and the `main`
branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use GitHub's
[private vulnerability reporting](https://github.com/asimfish/pdf-image-compressor/security/advisories/new)
to describe:

- the affected version or commit;
- the attack scenario and expected impact;
- reproducible steps or a minimal proof of concept;
- any suggested mitigation.

Do not include real credentials, private documents, or personal data. Use
synthetic fixtures where possible.

The maintainer aims to acknowledge a complete report within seven days, keep
discussion private while a fix is prepared, and credit the reporter in the
advisory unless anonymity is requested.

## Deployment responsibility

The public deployment profile limits upload size, page count, runtime, request
rate, and concurrency. Operators exposing PaperSqueeze to untrusted traffic
remain responsible for platform-level budgets, authentication, network
controls, logging, and timely dependency updates.
