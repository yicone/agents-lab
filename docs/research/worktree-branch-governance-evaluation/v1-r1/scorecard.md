# v1-r1 Trial Matrix and Scorecard

All status cells are intentionally empty (`—`). Do not populate them until the corresponding immutable trial record is complete and reviewed. Trials A, B, and C must each be fresh, user-created Codex tasks.

| Trial ID | Target | Archetype | Trial | Record validity | Contamination | Target integrity | Validator | Critical | Semantic | Include / exclude | Notes |
|---|---|---|:---:|---|---|---|---|---|---|---|---|
| `v1-r1-01` | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool | A | — | — | — | — | — | — | — | — |
| `v1-r1-02` | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool | B | — | — | — | — | — | — | — | — |
| `v1-r1-03` | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool | C | — | — | — | — | — | — | — | — |
| `v1-r1-04` | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool | A | — | — | — | — | — | — | — | — |
| `v1-r1-05` | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool | B | — | — | — | — | — | — | — | — |
| `v1-r1-06` | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool | C | — | — | — | — | — | — | — | — |
| `v1-r1-07` | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment | A | — | — | — | — | — | — | — | — |
| `v1-r1-08` | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment | B | — | — | — | — | — | — | — | — |
| `v1-r1-09` | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment | C | — | — | — | — | — | — | — | — |
| `v1-r1-10` | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server | A | — | — | — | — | — | — | — | — |
| `v1-r1-11` | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server | B | — | — | — | — | — | — | — | — |
| `v1-r1-12` | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server | C | — | — | — | — | — | — | — | — |
| `v1-r1-13` | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms | A | — | — | — | — | — | — | — | — |
| `v1-r1-14` | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms | B | — | — | — | — | — | — | — | — |
| `v1-r1-15` | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms | C | — | — | — | — | — | — | — | — |

## Aggregate gates

| Gate | Required | Status |
|---|---:|---|
| Valid included trials | `15` | — |
| Validator passes | `15/15` | — |
| Critical passes | `15/15` | — |
| Semantic passes overall | `>=13/15` | — |
| Semantic passes per archetype | `>=2/3` each | — |

## Held-out gate

These rows are not part of the 15-trial matrix and remain locked until all matrix gates pass.

| Target | Critical | Validator | Semantic | Core-schema change required? | Status |
|---|---|---|---|---|---|
| `/Users/tr/Workspace/elder-oasis` | — | — | — | — | — |
| `/Users/tr/Workspace/agent-storage-manager` | — | — | — | — | — |
