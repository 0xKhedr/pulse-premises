# Bundled frontend assets

These files are committed and copied into the SBMS Docker image by
`COPY static/ static/`. Page rendering does not need a CDN, Google Fonts,
public DNS, or a service worker cache. No asset downloads run during app
startup or the Docker build.

| Asset | Pinned package | License file |
| --- | --- | --- |
| Chart.js UMD bundle | `chart.js@4.5.1` | `chart.js-4.5.1/LICENSE.md` (MIT) |
| Intro.js JavaScript and CSS | `intro.js@7.2.0` | `intro.js-7.2.0/LICENSE.md` (AGPL-3.0) |
| DM Sans normal variable font, all supplied character subsets | `@fontsource-variable/dm-sans@5.3.0` | `dm-sans-5.3.0/LICENSE` (OFL-1.1) |
| Manrope normal variable font, all supplied character subsets | `@fontsource-variable/manrope@5.3.0` | `manrope-5.3.0/LICENSE` (OFL-1.1) |

Sources are the published npm archives:

- https://registry.npmjs.org/chart.js/-/chart.js-4.5.1.tgz
- https://registry.npmjs.org/intro.js/-/intro.js-7.2.0.tgz
- https://registry.npmjs.org/@fontsource-variable/dm-sans/-/dm-sans-5.3.0.tgz
- https://registry.npmjs.org/@fontsource-variable/manrope/-/manrope-5.3.0.tgz

Archive SHA-512 digests were checked against each pinned package's npm
`dist.integrity` before extraction. JavaScript source-map comments were
removed because the maps are not shipped. The font CSS combines the packages'
`wght.css` files, using the existing `DM Sans` and `Manrope` family names,
versioned local font URLs, and `font-display: swap`.

For an update, choose an explicit package version, verify its archive
integrity, copy the runtime files and license, and update this inventory.
Update the local URLs and the service worker's precache list/cache name.
If changing a shared stylesheet, also update its query version in templates
and importing stylesheets so previously cached CSS cannot retain old URLs.
Run `tests/test_frontend_assets.py`, then check a fresh browser session with
external requests blocked. Intro.js is the existing dependency; its license
has not changed.
