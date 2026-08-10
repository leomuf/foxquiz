# FoxQuiz Mascot Artwork

## License

The original FoxQuiz mascot artwork in this directory and its derived production
exports are dedicated to the public domain under **CC0 1.0 Universal**
(`CC0-1.0`), to the extent that copyright and related rights exist and can be
waived.

- Human-readable deed: https://creativecommons.org/publicdomain/zero/1.0/
- Legal code: https://creativecommons.org/publicdomain/zero/1.0/legalcode.en

This CC0 dedication applies only to:

- `assets/brand_sources/**`
- `app/static/assets/mascots/**`
- `app/static/assets/favicon.ico`
- `app/static/assets/favicon-16x16.png`
- `app/static/assets/favicon-32x32.png`
- `app/static/assets/apple-touch-icon.png`
- `app/static/assets/icon-192.png`
- `app/static/assets/icon-512.png`
- `app/static/assets/foxquiz_social_preview.png`
- `app/static/assets/foxquiz_social_preview.jpg`

All other repository content remains under the license stated in the top-level
`LICENSE` file. CC0 does not waive or grant trademark or patent rights.

## AI provenance notice

These assets were created on 2026-08-10 with OpenAI's image-generation tooling
from original FoxQuiz character directions. The selected direction was
"Variation 3": a warm storybook-explorer style for students in grades 5?12.

The design brief required three original characters without tracing or imitating
an operating-system emoji, vendor emoji set, existing mascot, brand character,
or named artist:

- Felix: an orange and cream fox explorer with a teal scarf and quiz map.
- Olivia: a purple owl scholar with round glasses, a teal cape, and study book.
- Dino: a turquoise and cream dragon explorer with coral horns and a question
  card.

Human contribution included defining the characters and constraints, selecting
Variation 3, reviewing the generated designs, directing the individual
face/full-body and group compositions, and approving their use in FoxQuiz.
Production processing removed flat chroma-key backgrounds, cleaned and
despilled the edges, created transparent square exports, resized browser/mobile
variants, and added the exact FoxQuiz wordmark to the social preview.

The high-resolution source masters are in `assets/brand_sources/mascots/`.
Deterministic production exports can be rebuilt with:

```bash
python assets/build_brand_assets.py
```

The builder requires Pillow. AI provenance is disclosed for transparency; it
does not add attribution requirements to the CC0 dedication.
