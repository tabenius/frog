# Shared observation fixtures

`task-observations.v1.json` is synthetic, versioned test data owned by Frog.
Consumers vendor identical bytes plus a SHA-256 and exact source commit. A schema
change needs a new fixture version. There is no runtime cross-repository import.

Profiles are explicit: Frog's model lane vocabulary and WeftMark's legacy-source
transition vocabulary are not interchangeable. In particular, `published` remains
an unknown legacy status requiring attention in WeftMark. Rebekah accepts the
read-only `source: frog` projection, not a writable `source: frog-model` diagnostic.
CF's public catalog must reject all private task projections. No fixture implies
ownership, freshness, successful verification, public disclosure or native claims.

Gateway tests derive timestamps from the fixed age offsets to avoid wall-clock
drift. Valid zoned timestamps are distinguished from naive/malformed ones, and
missing nullable fields are observations, never defaults granting readiness.
