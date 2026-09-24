# Apartment product palette

Updated 2026-09-25. KOREA REPLAY keeps a single apartment exploration interface and uses color to make its hierarchy visible.

| Meaning | Foreground | Surface |
| --- | --- | --- |
| Brand header | white | `#352b78` |
| Primary action / sale | `#5145cd` | `#f0edff` |
| Rent | `#087b72` | `#e5f5ef` |
| Selected map location | existing amber marker | pale amber location action |
| Land / water | warm neutral land | blue water |

The panel's `data-trade` attribute controls prices, chart points, active controls and selected table rows. Transaction text, pressed states, cancellation text and the existing cancelled-point color remain; color is not the only cue. Apartment cluster counts use the brand color and are not a price heatmap. Generic building shapes do not become classified apartment footprints through styling.

The palette is scoped to the atlas shell in `src/atlas-colors.css`. Both GeoJSON and vector basemaps use the same land/water/building colors. No new source, layer, dependency, animation or rendering loop is added.

Validation: production build and lint passed; 16 existing selection/vector/history tests passed. Desktop sale/rent screenshots and a 390px iframe responsive preview were inspected. This preview is not real-device touch testing. Calculated sRGB contrast ratios: white on sale 6.81:1, white on rent 5.14:1, sale price on tinted surface 5.92:1, rent price on tinted surface 4.56:1, white on header 11.88:1. These selected pairs are not a full accessibility audit or a performance target result.
