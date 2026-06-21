# Contributing

Thanks for taking the time to contribute — issues and pull requests are very welcome! 🎉

This is a small, focused integration, so almost anything helps:

- 🐛 **Bug reports** — open an [issue](https://github.com/pos-ei-don/ha-recorder-throttle/issues) with your Home Assistant version, what you expected, and what happened (logs help a lot).
- 💡 **Ideas & feedback** — new policy intervals, card improvements, edge cases you hit. Open an issue to discuss before a big PR.
- 🌍 **Translations** — add a `translations/<lang>.json` (copy `en.json` as a starting point).
- 🔧 **Code** — fixes and small features are great. Please keep changes focused.

## Pull requests

1. Fork the repo and create a branch.
2. Make your change. Keep the diff small and the style consistent with the surrounding code.
3. Make sure CI is green — the `Validate` workflow runs **hassfest** and **HACS** checks on every push/PR.
4. Open the PR with a short description of *what* and *why*.

No CLA, no ceremony. Be kind, assume good intent.

## Good to know

- The integration installs a **fail-safe** instance hook on an internal recorder method. If you touch that area, test that throttling still drops writes **and** that a failure leaves the recorder running unthrottled.
- Don't commit anything machine- or instance-specific (tokens, internal hostnames/IPs, real names).

## License

By contributing, you agree that your contributions are licensed under the project's [MIT License](LICENSE).
