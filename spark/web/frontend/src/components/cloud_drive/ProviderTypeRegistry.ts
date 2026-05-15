/**
 * Single source of truth for per-provider auth fields. Mirrors the
 * Pydantic discriminated union in
 * ``spark/plugins/builtins/cloud_drive.py``.
 */

export type ProviderKind = "drive" | "onedrive" | "dropbox" | "protondrive";

export type AuthFieldType = "text" | "secret" | "enum" | "info";

export interface AuthFieldSpec {
  /** Key inside the auth object. */
  key: string;
  /** Human label shown above the input. */
  label: string;
  /** UI control type. ``secret`` renders a vault-name input with the
   * "looks like a credential" warning; ``info`` renders read-only
   * helper text. */
  type: AuthFieldType;
  /** Default placeholder / value for new providers. */
  defaultValue?: string;
  /** One-line help under the label. */
  hint?: string;
  /** Enum-only: option list. */
  options?: { value: string; label: string }[];
  /** True if blank value blocks save when the provider is enabled. */
  required?: boolean;
  /** Free-text placeholder shown inside the empty input. */
  placeholder?: string;
}

export interface ProviderSpec {
  kind: ProviderKind;
  /** Friendly label for the type selector. */
  label: string;
  /** One-line description shown next to the type label. */
  blurb: string;
  /** Short pairing instructions — the Bootstrap card consumes these. */
  setup: { title: string; cmd?: string; note?: string }[];
  /** Auth field shape. */
  fields: AuthFieldSpec[];
  /** True if the provider's `auto_share` is actually wired in v1.
   * Drive only; the others surface the field but the post-put step
   * is a no-op with a logged warning until v2. */
  autoShareImplemented: boolean;
  /** Default `auth` payload when an operator first picks this kind. */
  defaultAuth: Record<string, unknown>;
}

export const PROVIDER_REGISTRY: Record<ProviderKind, ProviderSpec> = {
  drive: {
    kind: "drive",
    label: "Google Drive",
    blurb: "Personal Drive or a Shared Drive.",
    setup: [
      {
        title: "On a machine with a browser, run rclone authorize",
        cmd: 'rclone authorize "drive"',
        note: "rclone opens Google's OAuth consent screen. Approve. rclone prints a JSON blob.",
      },
      {
        title: "Save the JSON blob as a vault secret",
        cmd: "spark secrets set gdrive_token",
        note: "Paste the whole JSON (single line). The secret name goes in `token_secret` below.",
      },
      {
        title: "Optional: register your own OAuth client",
        note: "Avoids rclone's shared client rate-limits. See rclone.org/drive/#making-your-own-client-id.",
      },
    ],
    fields: [
      {
        key: "token_secret",
        label: "Token secret",
        type: "secret",
        required: true,
        placeholder: "gdrive_token",
        hint: "Name of the vault entry holding the OAuth JSON.",
      },
      {
        key: "client_id",
        label: "OAuth client ID (optional)",
        type: "text",
        placeholder: "123456-abcdef.apps.googleusercontent.com",
        hint: "Your own Google OAuth client. Strongly recommended.",
      },
      {
        key: "client_secret_secret",
        label: "OAuth client secret name (optional)",
        type: "secret",
        placeholder: "gdrive_client_secret",
        hint: "Vault entry holding the OAuth client secret. Required if client_id is set.",
      },
      {
        key: "team_drive",
        label: "Shared Drive ID (optional)",
        type: "text",
        placeholder: "",
        hint: "Empty = personal Drive. Otherwise the Shared Drive's ID.",
      },
    ],
    autoShareImplemented: true,
    defaultAuth: {
      kind: "drive",
      token_secret: "",
      client_id: "",
      client_secret_secret: "",
      team_drive: "",
    },
  },
  onedrive: {
    kind: "onedrive",
    label: "OneDrive",
    blurb: "Personal, Business, or SharePoint document library.",
    setup: [
      {
        title: "On a machine with a browser, run rclone authorize",
        cmd: 'rclone authorize "onedrive"',
        note: "OAuth consent through Microsoft. rclone prints a JSON blob.",
      },
      {
        title: "Save the blob as a vault secret",
        cmd: "spark secrets set onedrive_token",
      },
    ],
    fields: [
      {
        key: "token_secret",
        label: "Token secret",
        type: "secret",
        required: true,
        placeholder: "onedrive_token",
      },
      {
        key: "drive_type",
        label: "Drive type",
        type: "enum",
        defaultValue: "personal",
        options: [
          { value: "personal", label: "Personal" },
          { value: "business", label: "Business" },
          { value: "documentLibrary", label: "SharePoint Library" },
        ],
      },
      {
        key: "drive_id",
        label: "Drive ID",
        type: "text",
        placeholder: "",
        hint: "Required for Business / SharePoint.",
      },
      {
        key: "client_id",
        label: "OAuth client ID (optional)",
        type: "text",
      },
      {
        key: "client_secret_secret",
        label: "OAuth client secret name (optional)",
        type: "secret",
      },
    ],
    autoShareImplemented: true,
    defaultAuth: {
      kind: "onedrive",
      token_secret: "",
      drive_type: "personal",
      drive_id: "",
      client_id: "",
      client_secret_secret: "",
    },
  },
  dropbox: {
    kind: "dropbox",
    label: "Dropbox",
    blurb: "Personal or team Dropbox account.",
    setup: [
      {
        title: "On a machine with a browser, run rclone authorize",
        cmd: 'rclone authorize "dropbox"',
      },
      {
        title: "Save the blob as a vault secret",
        cmd: "spark secrets set dropbox_token",
      },
    ],
    fields: [
      {
        key: "token_secret",
        label: "Token secret",
        type: "secret",
        required: true,
        placeholder: "dropbox_token",
      },
      {
        key: "client_id",
        label: "OAuth app key (optional)",
        type: "text",
      },
      {
        key: "client_secret_secret",
        label: "OAuth app secret name (optional)",
        type: "secret",
      },
    ],
    autoShareImplemented: true,
    defaultAuth: {
      kind: "dropbox",
      token_secret: "",
      client_id: "",
      client_secret_secret: "",
    },
  },
  protondrive: {
    kind: "protondrive",
    label: "Proton Drive",
    blurb: "End-to-end encrypted. Auto-share unavailable.",
    setup: [
      {
        title: "Save the Proton account password as a vault secret",
        cmd: "spark secrets set proton_drive_password",
        note: "Use the Proton account password (or app-specific one if you have 2FA).",
      },
      {
        title: "If 2FA is enabled, save the TOTP seed too",
        cmd: "spark secrets set proton_drive_2fa",
      },
    ],
    fields: [
      {
        key: "username",
        label: "Username",
        type: "text",
        required: true,
        placeholder: "you@protonmail.com",
      },
      {
        key: "password_secret",
        label: "Password secret",
        type: "secret",
        required: true,
        placeholder: "proton_drive_password",
      },
      {
        key: "twofa_secret",
        label: "2FA secret (optional)",
        type: "secret",
        placeholder: "proton_drive_2fa",
        hint: "Vault entry holding the TOTP code or seed.",
      },
    ],
    autoShareImplemented: false,
    defaultAuth: {
      kind: "protondrive",
      username: "",
      password_secret: "",
      twofa_secret: "",
    },
  },
};

export const PROVIDER_KINDS: ProviderKind[] = [
  "drive",
  "onedrive",
  "dropbox",
  "protondrive",
];

export function providerLabel(kind: ProviderKind): string {
  return PROVIDER_REGISTRY[kind]?.label ?? kind;
}
