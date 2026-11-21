# Security policy

This repository is a portfolio banking simulation for fictional Minecraft currency. Do not use it to hold real money or sensitive identity documents.

## Reporting

Use GitHub's private vulnerability-reporting channel if the repository owner has enabled it. Otherwise contact the maintainer privately through their GitHub profile before sending sensitive details. Do not disclose credentials, real player data, production dumps, or an unremediated exploit in a public issue.

Describe the affected version, minimal local reproduction, expected/actual behavior, and impact. Use only synthetic accounts and isolated test systems. This project does not authorize scanning, load-testing, or exploiting any deployed service.

## Deployment boundary

Keep PostgreSQL, private gRPC, internal APIs, backups, and optional client secrets off the public network. Use distinct secrets, HTTPS, explicit trusted origins/proxies, and least-privilege administrative/account grants. Review third-party dependencies and integration trust assumptions before deployment.

Tests and scans provide evidence for specific checks at a point in time. They are not a formal security certification, a legal-compliance finding, or a promise that no vulnerabilities remain. Private audit material is not distributed here.

If a credential ever reaches a public commit, revoke or rotate it at its provider. Removing a file or creating a new commit does not make the old credential safe.
