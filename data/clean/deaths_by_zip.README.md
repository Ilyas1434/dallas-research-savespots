# deaths_by_zip.csv — intentionally header-only

ZIP/ZCTA-level overdose mortality **does not exist as a public product** for Dallas County.

- DSHS / CDC VSRR publish provisional overdose deaths at the **county** resolution only (see `deaths_dallas.csv`).
- The CDC NCHS tract product (`tract_overdose.geojson`) is **finer** than ZIP and is model-based on census-tract aggregation units.
- Producing honest ZIP figures would require a population-weighted tract->ZCTA crosswalk. The HUD USPS tract-ZIP crosswalk requires signup (gated), and ACS block-level population weights are not part of this module's verified sources. We therefore do **not** fabricate ZIP numbers.

## Resolution
This file is emitted with a header row only. Any ZIP-level comparison in Phase 3.5 must be produced via a **tract-to-ZIP spatial join** using `tract_overdose.geojson` geometries, and clearly labeled as a spatial approximation of a tract product (not an independent ZIP mortality measurement).
