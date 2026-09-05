# Teacher Face AI — V2 Brainstorm

## Core idea

Turn the project into a local, human-reviewed photo organization pipeline rather than a blind face-identification crawler.

```text
Official college sources
        ↓
Reference discovery
        ↓
Reference bank (name + image + provenance)
        ↓
Image quality / face validation
        ↓
Local photo indexing + clustering
        ↓
Candidate ranking (score + margin)
        ↓
Human review
        ↓
Confirmed teacher folders
```

## V2 changes already added

### 1. Multi-reference bank
`build_reference_bank.py` builds `output/reference_bank.json` from `refs.csv` and can store multiple official images for the same teacher. Optional `source_url` values are preserved as provenance.

### 2. Better candidate ranking
`match_groups_v2.py` ranks each local face group against every official reference image. It records the best reference score, a combined score, and the margin over the next candidate.

A `CANDIDATE` label is only a review aid. It is not an automatic identity decision.

### 3. One-command pipeline
`pipeline_v2.py` runs discovery → reference bank → local indexing → matching in one command.

## Next upgrades worth building

### Reference discovery engine
Replace the old broad site crawler with a strict source adapter for HETC. Accept only known faculty archive/profile URL patterns and keep every discovered profile in a manifest before downloading images.

Useful manifest fields:

- `name`
- `profile_url`
- `image_url`
- `source_type`
- `source_url`
- `discovered_at`
- `discovery_status`

### Reference image analyzer
Expand `analyze_references.py` into an image QA report with:

- exactly-one-face check
- face detection confidence
- image dimensions
- blur estimate
- face size relative to frame
- duplicate-image hash
- near-duplicate reference detection
- missing / broken image status

### Review queue
Make the Streamlit app operate as a queue:

`UNREVIEWED → CONFIRMED / REJECTED / SKIPPED`

Store decisions in a small local CSV/JSON ledger so restarting the app does not lose work.

### Safer matching logic
Use three signals rather than a single cosine score:

1. absolute similarity
2. margin over the second-best teacher
3. consistency across multiple photos/faces in the local group

Thresholds should be calibrated against the user's actual photo collection instead of treated as universal constants.

### Duplicate and group tools
Add image hashing and perceptual-hash detection so repeated copies of the same photo do not inflate a teacher's group.

### Audit trail
Every final organization action should record:

- original path
- destination path
- teacher name entered by user
- candidate suggested by model
- score / margin
- timestamp
- reviewer decision

### Important boundary
Reference discovery should remain limited to official/public college sources selected by the user. Do not turn this into an unrestricted face search across the internet.
