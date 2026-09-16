# Contributing

Thanks for helping. A few things keep this project trustworthy:

- **Metadata only.** The collector must never read or upload values, command arguments, environment variables, message bodies, credentials or absolute home paths. A new field means a new server validation rule *and* a test.
- **No personal data in the repo.** Fixtures use fictional names. CI greps for home paths.
- **Both languages.** UI strings live in `src/locales/en.json` and `src/locales/ja.json`; add keys to both.
- **Tests before claims.** `npm run build && npm test && python3 -m unittest discover -s collector/tests -t .`
- **Small PRs** with a sentence on *why*.

Bugs and ideas: open an issue. Security problems: see `SECURITY.md`.
