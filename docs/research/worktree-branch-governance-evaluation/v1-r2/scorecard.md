# v1-r2 Trial Matrix and Scorecard

All status cells are intentionally empty (`—`). Do not populate them until the corresponding immutable trial record is complete and reviewed. Each trial must be a fresh, user-created Codex task. Round A is authorized; Rounds B and C remain locked until the mandatory post-trial-05 gate is reviewed and explicitly authorized.

| Trial ID | Round | Target | Archetype | Record validity | Contamination | Target integrity | Validator | Critical | Semantic | Quota checkpoint | Include / exclude | Notes |
|---|:---:|---|---|---|---|---|---|---|---|---|---|---|
| `v1-r2-01` | A | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool | — | — | — | — | — | — | — | — | — |
| `v1-r2-02` | A | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool | — | — | — | — | — | — | — | — | — |
| `v1-r2-03` | A | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment | — | — | — | — | — | — | — | — | — |
| `v1-r2-04` | A | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server | — | — | — | — | — | — | — | — | — |
| `v1-r2-05` | A | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms | — | — | — | — | — | — | — | — | — |
| `v1-r2-06` | B | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool | — | — | — | — | — | — | — | — | — |
| `v1-r2-07` | B | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool | — | — | — | — | — | — | — | — | — |
| `v1-r2-08` | B | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment | — | — | — | — | — | — | — | — | — |
| `v1-r2-09` | B | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server | — | — | — | — | — | — | — | — | — |
| `v1-r2-10` | B | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms | — | — | — | — | — | — | — | — | — |
| `v1-r2-11` | C | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool | — | — | — | — | — | — | — | — | — |
| `v1-r2-12` | C | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool | — | — | — | — | — | — | — | — | — |
| `v1-r2-13` | C | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment | — | — | — | — | — | — | — | — | — |
| `v1-r2-14` | C | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server | — | — | — | — | — | — | — | — | — |
| `v1-r2-15` | C | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms | — | — | — | — | — | — | — | — | — |

## Mandatory post-trial-05 gate

Trial `06` must not start until this gate is complete and Rounds B/C are explicitly authorized.

| Required review | Evidence | Decision |
|---|---|---|
| Quota checkpoints present for trials `01`-`05` | — | — |
| Actual usage and remaining quota reviewed | — | — |
| Trial-record required fields stable | — | — |
| Report schema and validator behavior stable | — | — |
| Authorization for Rounds B/C | — | — |

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
