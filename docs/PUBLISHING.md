# Publishing checklist

## Brand icon — already done, no submission needed

Since **Home Assistant 2026.3.0**, custom integrations serve their **own** brand images
from a local `brand/` folder; they are no longer submitted to `home-assistant/brands`
(that repo's bot auto-closes custom-integration icon PRs now). Local brand images take
priority over the brands CDN — no extra configuration.

This repo already ships them at:

- `custom_components/recorder_throttle/brand/icon.png` — 256×256, transparent, square, trimmed
- `custom_components/recorder_throttle/brand/icon@2x.png` — 512×512, transparent, square, trimmed
- `custom_components/recorder_throttle/brand/icon.svg` — editable source

Home Assistant serves them at `/api/brands/integration/recorder_throttle/icon.png`.
Optional extra files HA recognizes: `dark_icon.png`, `logo.png`, `dark_logo.png`, and their `@2x` variants.

Reference: <https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api>

---

## HACS default inclusion

Reference: <https://hacs.xyz/docs/publish/include> and <https://hacs.xyz/docs/publish/start>

Pre-flight (all already met in this repo):

- [x] Public repository
- [x] Repository **description** set
- [x] Repository **topics** set
- [x] Issues enabled
- [x] `README.md` present
- [x] `hacs.json` with a `name`
- [x] At least one **release** (`v0.3.0`)
- [x] Own brand icon in `custom_components/recorder_throttle/brand/` (replaces the old brands submission)
- [x] GitHub Actions: `hassfest` **and** `hacs/action` validation passing

Steps:

1. Fork [`hacs/default`](https://github.com/hacs/default).
2. Add `pos-ei-don/ha-recorder-throttle` to the **`integration`** file, keeping the list alphabetically sorted.
3. Open the PR. A bot runs validation; address anything it flags.
4. After merge, the integration is installable from HACS without adding it as a custom repository.
