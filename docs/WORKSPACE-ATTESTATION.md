# Workspace attestation ownership

**Owner: [WeftMark](https://github.com/tabenius/WeftMark).** The former loose
workspace-root `attest-workspace.py` now lives in that repository at
`scripts/attest-workspace.py`. The accompanying `attestations/` directory moved
with it, including the historical manifest, chain, detached signature, exported
public keys, and the `VERIFY.md` and `GUARANTEES.md` guides.

The historical signed files were moved byte for byte. They describe the workspace
on 2026-10-02, not the current checkout. The signed chain locator
`attestations/workspace.jsonl` remains valid relative to the WeftMark repository.

## Responsibilities

| Concern | Owner |
| --- | --- |
| Box identity, repo registry, dependency edges, task/lock coordination | Frog |
| Workspace collection and signed delivery bundle | WeftMark |
| Chain canonicalisation, digest rules and chain verification | Nostoi |
| Private signing keys | Operator's keyring, outside repositories |

Frog's `repo_deps` are coordination data. A workspace attestation records repo
revisions and runtime observations; it is not an installer or a resolved
transitive Python dependency SBOM. The repositories' `uv.lock` files supply
the latter dependency resolution.

## Collect a new bundle

From the workspace root, select the actual WeftMark checkout path. The current
sibling layout uses `$PWD/weftmark`; the historical `/data/src` layout documented
in the README uses `$PWD/experiments/WeftMark`. Paths are case-sensitive, so
substitute your checkout path rather than assuming a directory name:

```bash
WEFTMARK_REPO="${WEFTMARK_REPO:-$PWD/weftmark}"
# Historical layout: WEFTMARK_REPO="$PWD/experiments/WeftMark"
python3 "$WEFTMARK_REPO/scripts/attest-workspace.py" \
  --workspace "$PWD" \
  --output /path/to/delivery/attestations
```

The collector obtains the stable box ID from `frog --json box whoami`; use
`--box-id` for an explicit identity. `--nostoi-python` overrides the sibling
`nostoi/contrib/python/nostoi.py` reference path. Signing uses the operator's
GPG keyring; select identities with `--signing-key`, `--org-root`, and
`--principal`. Defaults retain the original box-genesis identities.

Choose a new output directory for each dated delivery. Existing generated bundle
files cause a refusal unless `--replace` is explicitly supplied. Do not use
replacement on WeftMark's committed historical bundle.

## Verify

Follow WeftMark's `attestations/VERIFY.md` and `attestations/GUARANTEES.md`.
Have WeftMark's verification script and a trusted local Nostoi Python reference
available before going offline; the historical bundle does not embed a verifier.
Verify the detached signature and pinned signer fingerprint, then verify the
Nostoi chain and its binding to the signed attestation's sequence and digest.
A fingerprint claimed by the JSON is insufficient: compare GPG's actual signer
status, and bind `manifest.json` to the body of the signed chain record.
A valid signature establishes integrity/authorship, not freshness or code quality.
