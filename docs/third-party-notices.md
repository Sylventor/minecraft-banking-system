# Licensing and third-party notices

The project owner selected **Apache License 2.0** for this portfolio release. `LICENSE` contains its unmodified text. `NOTICE` records retained notices, and `LICENSES/LicenseRef-Original-MIT.txt` preserves the original MIT grant for previously MIT-distributed code. Existing grants are not revoked by choosing a license for this release.

Dependencies retain their own terms. The following is a component guide, not a replacement for the license metadata of the exact package/image being redistributed:

| Component | Upstream license family |
| --- | --- |
| React, Vite, TypeScript tooling, FastAPI, Pydantic, SQLAlchemy, httpx, Mineflayer, vec3 | MIT or package-specific permissive notices |
| gRPC, Protocol Buffers, Google Test | Apache-2.0/BSD notices as identified by each upstream distribution |
| libpqxx | BSD-3-Clause |
| PostgreSQL/libpq | PostgreSQL License |
| nginx | BSD-style upstream license |
| Fabric Loader / Fabric API | Apache-2.0 |
| Yarn mappings | CC0-1.0 |
| psycopg | LGPL-3.0-or-later; binary distributions also contain third-party notices |
| certifi | MPL-2.0 (verify its bundled certificate-data notices) |
| caniuse-lite browser data | CC-BY-4.0 attribution terms |
| Argon2 and Python bindings | Consult the exact binding/library distribution notices |

Review package metadata, native library notices, base-image packages, and their source/redistribution requirements before shipping binaries or container images. Dependency manifests and lockfiles identify the selected versions. They do not transfer ownership of those dependencies.

The original production logo is excluded. No replacement commercial logo or fictional company identity is created. Demo screenshots are captures of the application using synthetic data. No Minecraft game jar, texture pack, Microsoft authentication credential, or third-party binary is committed. Obtain the game and integration dependencies from their authorized providers.

The Minecraft Banking System name is a descriptive portfolio title; no trademark-clearance determination is claimed. Operators must separately confirm rights in their own logos, content, and imported resources.

References: [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0), [MIT License](https://choosealicense.com/licenses/mit/), and each dependency's upstream LICENSE/NOTICE files.

A [verification-time license inventory](dependency-licenses.json) records installed package metadata. It is not a complete SBOM for a future container or binary distribution. Missing or ambiguous metadata requires upstream review.
