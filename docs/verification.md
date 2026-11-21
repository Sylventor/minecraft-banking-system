# Verification record

Prepared locally on **21 November 2026**. These results describe this source copy and the available test environment, not a certification or a production penetration test.

| Check | Result |
| --- | --- |
| Native Financial Core, MSVC Release build | Passed |
| Native CTest suite | 5 passed |
| API, SDK, and Discord unit tests | 84 passed; 1 POSIX-only check skipped on Windows |
| Selected API/financial integration suite, fresh PostgreSQL test database and real core | 124 passed |
| Web Dashboard component/localization tests | 73 passed across 23 files |
| Frontend TypeScript/build and ESLint | Passed |
| Minecraft ATM unit tests | 42 passed |
| Fabric HPay client, Java 21 / Minecraft 1.21.11 | Gradle build passed using cached dependencies |
| nginx configuration syntax, nginx 1.30.7 | Passed using a test copy with local log paths |
| Python dependency audit of the installed verification environment | No known findings after dependency updates |
| Frontend dependency audit | No known findings after the lockfile update |
| ATM dependency audit | No known findings after the lockfile update |
| Public source boundary and English-documentation checks | Passed |
| Public Gitleaks scan | Passed with exact-value/path exceptions for three synthetic fixtures |
| Synthetic browser screenshots | Desktop dashboard, virtual accounts, mobile dashboard; no page errors or horizontal mobile overflow |
| Local environment generator | Independent random secrets and refusal to overwrite an existing file verified |

The integration selection covered financial operations, authorization/IDOR, database integrity, internal transfers, payment requests, exchange, organization-owner approvals, ATM operations, HPay, password authentication, persistent sessions, news, support, privacy, ATM capabilities, and Minecraft verification. It did not run every historical upgrade scenario or a sustained load test.

The core was compiled from this copy using the pinned vcpkg dependencies. Docker uses Linux distribution packages, so the successful native build does not establish that every container image builds. The environment had no Docker engine/CLI; Compose startup, image builds, image CVE scans, and Linux container permissions still require verification on an isolated Docker host. The optional mod build had no separate Java test cases.

Python tests emitted upstream deprecation warnings. The frontend build emitted the existing large-chunk warning. Those warnings did not fail the checks. Dependency scans are time-specific and do not cover every transitive native library, future base-image update, or Java/game dependency.

Screenshots used the real dashboard and core against newly generated fictional accounts. No production export, provider credentials, existing browser profile, or live Minecraft server was used. The checked-in account labels and balances are demonstration data, not real users.

GitHub publication, repository visibility, real provider logins, production infrastructure, and live physical-item transfers were not exercised. Before an actual deployment or publication, rerun the checks for the exact target environment and follow [the publication checklist](publication.md).
