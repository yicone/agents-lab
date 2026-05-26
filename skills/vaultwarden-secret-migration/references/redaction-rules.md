# Redaction Rules

Use these rules when converting a secret-bearing Logseq block into a Vaultwarden reference.

## Keep

- service name
- environment such as `prod`, `staging`, `personal`
- account identifier if non-sensitive
- permission scope summary
- creation or expiry date
- rotation instructions
- last 4 characters only, if useful for matching

## Remove

- the full token or password
- any prefix + suffix combination that exposes most of the value
- QR codes or otpauth URLs
- PEM payloads
- session cookies
- full database URLs with embedded credentials

## Safe Templates

- `Token stored in Vaultwarden: OpenAI / prod / last4=7KQ2`
- `Password stored in Vaultwarden: GitHub / yicone@gmail.com`
- `Secret stored in Vaultwarden: PostgreSQL / app user / prod`
- `Private key stored in Vaultwarden: VPS deploy key / bw.yic.one`

## Rotation Guidance

Recommend rotation when:

- the note was synced to GitHub, even if the repository was private
- the note existed in shared cloud storage
- the secret appeared in chat transcripts, screenshots, exports, or backups
- the note was ever exposed to third-party plugins or publishing flows

Rotation is lower urgency, but still worth considering, when:

- the secret only lived in a local encrypted disk and never left the machine

## Logseq Write Guidance

- Prefer editing the original block instead of adding a second block that leaves the secret above
- If a page has multiple secret-bearing blocks, treat each one independently
- Keep one replacement line per secret so later cleanup and auditing are easy
