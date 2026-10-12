# Frog repository instructions

Frog owns workspace coordination: repository discovery and identity, declared
dependency edges, tasks, locks, and SSH federation. Use the repository's
`README.md` and `docs/` for command and coordination contracts.

## Workspace attestations are owned by WeftMark

The workspace collector lives in the WeftMark repository at
`scripts/attest-workspace.py`; its historical signed bundle and verification
instructions live at `attestations/`. Do not create another collector or
signing implementation in Frog. Frog supplies coordination/box identity;
WeftMark owns collection and delivery; Nostoi supplies chain canonicalisation
and verification. Private signing keys remain outside all repositories.

See [docs/WORKSPACE-ATTESTATION.md](docs/WORKSPACE-ATTESTATION.md) for explicit
workspace/output arguments, historical-bundle handling, and verification.
