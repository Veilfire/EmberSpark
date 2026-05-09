# Secrets Guide

EmberSpark stores secrets in a single **age-encrypted vault file** at
`~/.spark/secrets.age`. The vault's identity lives at
`~/.spark/age_identity.key` (mode `0600`). An optional **env var
fallback** handles the dev/CI case.

> H1.3 changed this: the old `keyring` backend was removed. Everything
> now goes through the age vault.

## The vault at a glance

| Path | What it is |
|---|---|
| `~/.spark/secrets.age` | Age-encrypted JSON `{name: value}` vault |
| `~/.spark/age_identity.key` | Unwrapped age identity (mode `0600`) |
| `~/.spark/age_identity.key.age` | Passphrase-wrapped identity (only when you use `--passphrase`) |

- **One vault, one identity, one operator.** Multi-user access is out of
  scope.
- **Zero cloud.** No KMS, no network. `pyrage` is a pure-Python binding
  to `age-rs`.
- **Backup-friendly.** The whole vault is one file. `cp ~/.spark/secrets.age
  backup/` and you're done (don't forget the identity file).

## First boot

`spark serve` auto-creates the vault + identity if they don't exist. You
see a log line like:

```
age_vault_auto_initialized vault_path=/home/you/.spark/secrets.age passphrase_wrapped=False
```

From there, use the CLI to populate it:

```bash
spark secrets set anthropic_key    # prompts (no echo)
spark secrets set openai_key       # prompts (no echo)
spark secrets list                 # names only, never values
```

## CLI reference

```
spark secrets list                         # print stored secret names
spark secrets set <name>                   # prompts for value (no echo)
spark secrets set <name> --value <value>   # inline (avoid in shell history)
spark secrets delete <name>                # remove a secret
spark secrets init-age-vault [--passphrase] [--force]
                                           # pre-provision, passphrase-wrap,
                                           # or rebuild from scratch
spark secrets rotate-vault-key [--passphrase]
                                           # new identity, re-encrypt vault
spark secrets healthcheck                  # probe vault + env fallback
```

## Passphrase-wrapped identity

If filesystem permissions aren't enough for your threat model, you can
**passphrase-wrap** the age identity with an outer age layer (scrypt
KDF). The wrapped identity file is at `~/.spark/age_identity.key.age`
instead of the unwrapped version.

```bash
spark secrets init-age-vault --passphrase --force
```

Subsequent `spark serve` starts will need the passphrase:

- **Interactive**: prompted at startup via `getpass`
- **Daemon**: set `SPARK_AGE_PASSPHRASE` in the environment, or configure
  `spec.secrets.age_file.passphrase_env: SPARK_AGE_PASSPHRASE` in
  `~/.spark/spark.yaml`

Tradeoff: passphrase wrap raises the bar against a host read-only
attacker (someone who can read files but not run as you), but it adds
operational friction — every daemon restart needs the passphrase.
Default is **unwrapped**, protected by filesystem permissions alone.

## Env var fallback

For development and CI, you can still set secrets via environment
variables. The manager checks `SPARK_SECRET_<UPPERCASE_NAME>` as a
second lookup when a secret isn't in the vault.

```bash
export SPARK_SECRET_ANTHROPIC_KEY='sk-ant-...'
```

**Caveats:**

- **The vault always wins.** If a name exists in both places, the vault
  value is returned.
- **Every env fallback resolution is logged** at `info` severity with
  the secret name and a remediation hint. Useful in CI; annoying in
  prod. Turn it off via `spec.secrets.env_fallback.warn_on_hit: false`.
- **Can be disabled entirely.** Set
  `spec.secrets.env_fallback.enabled: false` to refuse env lookups and
  raise `SECRET_NOT_FOUND` for missing vault entries.

## Rotating the vault key

`spark secrets rotate-vault-key` generates a new age identity, re-encrypts
the vault under it, and deletes the old identity file. Safe to run at any
time; the operation is atomic (crash mid-rotation leaves the vault still
decryptable with the new identity).

```bash
spark secrets rotate-vault-key               # unwrapped
spark secrets rotate-vault-key --passphrase  # new passphrase-wrapped identity
```

## Runtime config

```yaml
spec:
  secrets:
    age_file:
      vault_path: ~/.spark/secrets.age
      identity_path: ~/.spark/age_identity.key
      passphrase_env: null            # or: SPARK_AGE_PASSPHRASE
      auto_init: true                 # auto-create on first boot
    env_fallback:
      enabled: true
      warn_on_hit: true
```

## Threat model

**Protects against:**

- Accidental commit of secrets to git (they live in an encrypted file)
- Casual filesystem inspection (0600 + encryption)
- Process memory dumps that don't also capture the decrypted vault
  (the decrypted dict is in-memory only while the daemon runs)
- Backup exfiltration (the backup is age-encrypted; without the identity
  file the attacker has nothing)

**Does NOT protect against:**

- A compromised local user account. If an attacker can read
  `age_identity.key` + `secrets.age` AND run code as your user, they
  have full access. That's outside the threat model — the host is
  trusted.
- Cold boot / memory attacks. Decrypted values live in-memory as
  `SecretStr` wrappers. They're redacted in logs but not erased from
  RAM on exit.
- Kernel exploits. Same boundary as the rest of the sandbox story.

## Further reading

- [Concepts: Privacy](Concepts-Privacy) — redaction pipeline
- [Security Center Guide](Security-Center-Guide) → Secrets tab — the
  web UI view (names + canary test)
- [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md)
  — full threat model
