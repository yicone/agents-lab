# bw CLI Notes

Use the official Bitwarden CLI to write items into self-hosted Vaultwarden.

## Minimum Checks

- `bw --version`
- `bw status`
- `bw config server`

Expected server for this environment:

- `https://bw.yic.one`

## Authentication Guidance

- If `bw status` is `unauthenticated`, use `bw login`
- If `bw status` is `locked`, use `bw unlock --raw`
- Use `BW_SESSION` only for the current shell session
- Prefer `bw lock` when finished

Do not:

- write `BW_SESSION` into Logseq
- commit `BW_SESSION` to files
- capture the master password in scripts unless the user explicitly requests an automation path and accepts the risk

## Common Creation Pattern

Create a secure note:

```bash
bw get template item \
  | jq '.type=2 | .name="OpenAI / prod" | .notes="API token\\nlast4=7KQ2"' \
  | bw encode \
  | bw create item
```

Create a login item:

```bash
bw get template item \
  | jq '.type=1 | .name="GitHub / yicone@gmail.com" | .login.username="yicone@gmail.com" | .login.password="..." | .login.uris=[{"match":0,"uri":"https://github.com"}]' \
  | bw encode \
  | bw create item
```

Create a generic secret note with tags:

```bash
bw get template item \
  | jq '.type=2 | .name="PostgreSQL / app user / prod" | .notes="password stored for prod DB user" | .fields=[{"name":"kind","value":"database-password","type":0}] | .tags=["secret","prod"]' \
  | bw encode \
  | bw create item
```

## Mapping Advice

- API tokens: prefer secure note unless a login item is clearly better
- Website credentials: prefer login item
- Recovery codes: secure note
- Private keys: secure note, or attachment workflow if needed later

## Post-Create Checklist

- `bw sync`
- record the final Vaultwarden reference text for the Logseq note
- remove the raw secret from the note
- recommend rotation if the old note was widely synced
