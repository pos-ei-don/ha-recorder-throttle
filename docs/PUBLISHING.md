# Publishing checklist

Two separate things, in this order:

1. **Brand assets** → PR to [`home-assistant/brands`](https://github.com/home-assistant/brands)
2. **HACS default inclusion** → PR to [`hacs/default`](https://github.com/hacs/default)

Step 1 is a prerequisite for step 2 (HACS validates that the integration's brand exists).

---

## 1. Submit the icon to `home-assistant/brands`

The icon already lives in this repo at:

- `custom_components/recorder_throttle/brand/icon.png` — 256×256, transparent
- `custom_components/recorder_throttle/brand/icon@2x.png` — 512×512, transparent

> While the brand is not yet merged upstream, HACS uses this local `brand/` folder as a fallback, so the icon already shows in HACS.

To get it into Home Assistant core (so it appears in the integrations UI for everyone):

1. Fork `home-assistant/brands`.
2. Because this is a **custom** (not-in-core) integration, place the files under:
   ```
   custom_integrations/recorder_throttle/icon.png      (256×256)
   custom_integrations/recorder_throttle/icon@2x.png   (512×512)
   ```
   (Copy the two files from this repo's `brand/` folder.)
3. Requirements (already met by our files): square, transparent PNG, exact sizes, trimmed.
4. Open the PR. CI in the brands repo validates the assets automatically.

Logo files (`logo.png` / `logo@2x.png`) are optional — the icon alone is enough.

---

## 2. Submit to HACS default

Reference: <https://hacs.xyz/docs/publish/include> and <https://hacs.xyz/docs/publish/start>

Pre-flight (all already done in this repo):

- [x] Public repository
- [x] Repository **description** set
- [x] Repository **topics** set
- [x] Issues enabled
- [x] `README.md` present
- [x] `hacs.json` with a `name`
- [x] At least one **release** (`v0.3.0`)
- [x] GitHub Actions: `hassfest` **and** `hacs/action` validation passing
- [ ] Brand merged into `home-assistant/brands` (step 1) — **do this first**

Then:

1. Once the brands PR is merged, remove `ignore: brands` from `.github/workflows/validate.yml` and confirm the HACS check still passes without it.
2. Fork [`hacs/default`](https://github.com/hacs/default).
3. Add `pos-ei-don/ha-recorder-throttle` to the **`integration`** file, keeping the list alphabetically sorted.
4. Open the PR. A bot runs the same validation; address anything it flags.
5. After merge, the integration is installable from HACS without adding it as a custom repository.
