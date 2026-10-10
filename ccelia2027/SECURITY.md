# Security Policy

## Reporting a Vulnerability

Do not report security vulnerabilities in a public issue or pull request. Use the repository's **Report a vulnerability** action to submit a private report. If private vulnerability reporting is unavailable, contact the repository owner privately through GitHub before sharing technical details.

Include only the information needed to reproduce and assess the issue. Do not include real credentials, personal data, or production secrets. Do not test against systems or accounts you do not own.

## Disclosure

The maintainer will acknowledge reports and coordinate a reasonable fix and disclosure timeline with the reporter. Please allow time for a fix before public disclosure. No response-time guarantee is made for this initial project foundation.

## Supported Versions

There are no tagged releases yet. The latest `main` branch is the only development line; no stable release or support window is claimed.

## Secret Handling

Use GitHub Actions secrets for deployment credentials and local `.dev.vars` for Wrangler development. Never commit access tokens, API keys, private keys, `.env` files, or user data. If a secret is exposed, revoke it at its issuer immediately and report the incident privately.
