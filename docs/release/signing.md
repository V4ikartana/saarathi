# Signing and notarization

Release builds work unsigned, but each OS warns on first launch (see [installing.md](installing.md)). Signing removes
the warnings. It needs credentials only the project owner can obtain; the release workflow already uses them when the
GitHub secrets below exist, and skips signing otherwise.

## macOS: Developer ID and notarization

Needs an Apple Developer Program membership (USD 99 per year) and a "Developer ID Application" certificate.

| Secret | Value |
|---|---|
| `APPLE_CERTIFICATE` | The certificate and private key exported as `.p12`, base64-encoded (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | The `.p12` export password |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | The Apple ID email used for notarization |
| `APPLE_PASSWORD` | An app-specific password for that Apple ID |
| `APPLE_TEAM_ID` | The 10-character team id |

The app runs under the hardened runtime with one entitlement, `com.apple.security.cs.disable-library-validation`,
because the bundled engine loads the Python library it unpacks at start (`desktop/src-tauri/entitlements.plist`).
Without signing secrets the bundle is ad-hoc signed (`signingIdentity: "-"`).

## Windows: Authenticode

Needs a code-signing certificate from a certificate authority (an OV certificate, or Azure Trusted Signing). Tauri
signs with `bundle.windows.certificateThumbprint` or a `signCommand`; add the certificate to the runner and set one of
them in `tauri.conf.json`. This is not wired yet because the certificate type decides the method.

## Linux

Packages are unsigned; `SHA256SUMS.txt` in each release lets people verify downloads.
