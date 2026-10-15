# Security

This application is designed for a trusted home LAN. It has no built-in
login, authorization, CSRF protection or TLS. Anybody who can reach its HTTP
port may control the Sony, change playlists and radio presets, and download
music from the configured library. Do not expose the port directly to the
Internet. Remote access needs a separately secured network or gateway.

Use a regular user account with read access to the music library and write
access only to its own application state. Local configuration, radio URLs,
playlists, logs and generated media must not be committed. Check staged files
with `python scripts/check_public_files.py` before publishing.

Never post tokens, credentials, private configuration or full personal media
paths in public issues. Describe the problem with redacted examples. If the
repository offers GitHub's private vulnerability reporting, use it for
security-sensitive reports; otherwise ask the maintainer for a private channel
without disclosing exploit details in public.
