# Security

S-Initiative alpha is intended for trusted peers and LAN/VPN use. Direct HTTP is not
an Internet-facing security boundary. Connect tokens are Base64, not encryption.
Experimental SFTP descriptors may contain bearer credentials; use a dedicated,
jailed, least-privilege relay account and never commit local configuration.

Report vulnerabilities through GitHub private vulnerability reporting when
enabled, not a public issue.
