# Brand sources

`icon.svg`, `logo.svg` and `dark_logo.svg` are the sources for the PNGs in
`custom_components/waterlevel_ie/brand/`, which Home Assistant and HACS show for the
integration. The logo text is Inter Bold.

| File | Size |
| --- | --- |
| `icon.png` / `icon@2x.png` | 256 × 256 / 512 × 512 |
| `logo.png` / `logo@2x.png` | 128 / 256 high, trimmed |
| `dark_logo.png` / `dark_logo@2x.png` | as the logo, for dark themes |

To regenerate, render each SVG to PNG at the `@2x` size with a transparent background
(for example in a headless browser), trim the logo to its content, and scale it to half
for the plain file.
