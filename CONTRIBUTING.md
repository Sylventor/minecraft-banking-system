# Contributing

Use a local development environment and synthetic data. Never include a real `.env`, player records, Minecraft/Discord credentials, database dumps, logs, or backups in an issue or pull request.

Read [Architecture](docs/architecture.md), [Financial model](docs/financial-model.md), and [Testing](docs/testing.md) before changing financial or authorization code. Keep money exact, preserve idempotency, and enforce account/organization permissions in the core. Do not replace a financial operation with direct SQL balance edits.

Keep documentation, new/modified comments, and docstrings in English. Preserve the application's Russian, English, and Ukrainian localization and never translate user-authored content. Add meaningful regression tests for security and financial behavior changes.

Explain the problem, resulting behavior, tests performed, and relevant limitations in the pull request. Disclose significant AI assistance accurately; contributors remain responsible for reviewing and testing the submitted code. Do not claim tests passed unless they were run.

Use descriptive Conventional Commit messages. Work respectfully, report security issues privately under [SECURITY.md](SECURITY.md), and retain all required copyright/license notices. Contributions must be compatible with Apache-2.0 and the included third-party notices.
