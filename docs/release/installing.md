# Installing Saarathi Desktop

Saarathi Desktop ships with its engine built in: no Python, `uv` or terminal setup is needed. You do need at least one
provider CLI installed and signed in with your subscription:

| Provider | Install | Sign in |
|---|---|---|
| Claude Code | <https://code.claude.com/docs/en/setup> | `claude auth login` |
| Codex CLI | `npm install -g @openai/codex` | `codex login` (ChatGPT) |
| Antigravity CLI | <https://antigravity.google> | run `agy` once |

Open the app, choose a Git repository, and let Saarathi set it up. Settings → Providers → Check providers confirms what
it found.

## macOS (Apple Silicon)

1. Open `Saarathi_<version>_aarch64.dmg` and drag Saarathi to Applications.
2. Until releases are signed with an Apple Developer ID, macOS blocks the first launch. Open Saarathi once, then go to
   System Settings → Privacy & Security and choose **Open Anyway** (or Control-click the app and choose **Open**).
   This is needed only once. Builds are ad-hoc signed, so macOS can verify they were not altered after building.

## Windows

1. Run `Saarathi_<version>_x64-setup.exe` (installs for your user, no administrator rights) or the `.msi`.
2. Until releases are signed with a code-signing certificate, SmartScreen shows "Windows protected your PC". Choose
   **More info → Run anyway**.

## Linux

- Debian and Ubuntu: `sudo apt install ./saarathi_<version>_amd64.deb`
- Other distributions: `chmod +x Saarathi_<version>_amd64.AppImage` and run it. Requires WebKitGTK 4.1.

## Verifying a download

Each release lists `SHA256SUMS.txt`. Compare with `shasum -a 256 <file>` (macOS, Linux) or
`Get-FileHash <file>` (Windows PowerShell).

## For developers

The app looks for the engine in this order: `SAARATHI_BRIDGE` (a path to a `saarathi` executable), the engine bundled
next to the app, then `saarathi` on your login shell's PATH. Settings → Engine shows which one is in use.
