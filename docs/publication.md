# Safe publication checklist

This directory is a separate local repository with a new root commit. Preparing it does not create a GitHub repository, publish source, or change another repository's visibility.

Proposed remote repository name: **`minecraft-banking-system`**. Public project title: **Minecraft Banking System**.

1. Review the private audit outside this directory and resolve or explicitly accept its remaining release conditions. Do not copy that report into a public issue or commit.
2. Confirm the Apache-2.0 choice, retained original notices, and rights to included content.
3. Check that screenshots contain only fictional users and the neutral display name.
4. Run `python tools/verify_public_release.py`, source/history secret scanners, builds, and the relevant tests. Inspect the staged file list.
5. Check `git remote -v`, `git log --all`, and `git rev-list --max-parents=0 --all`. No remote from the production repository or inherited private objects should be present.
6. Manually confirm that the original production repository remains private. A missing repository response from an integration is not proof of visibility.
7. If the owner explicitly approves publication, create an **empty** repository in their GitHub account with the proposed name. Review visibility deliberately. Do not import or fork the private history.
8. Only after that approval, add the new destination and perform a normal push from this directory:

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/minecraft-banking-system.git
git push -u origin main
```

Do not execute these publication commands as part of local preparation. They are instructions for a later owner-approved action. Never use force-push to overwrite a remote history.

No production deployment should depend on this public copy existing, being accessible, or being synchronized. Keep production configuration and operational data in their existing private systems.
