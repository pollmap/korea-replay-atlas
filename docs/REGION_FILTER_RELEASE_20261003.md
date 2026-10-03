# Audited property publication and selected-period region metrics — 2026-10-03

## Change
The map previously accepted only the latest single-month, unrestricted region metric. The default 36-month / 84㎡ band therefore displayed an aggregation placeholder even when transactions were available. Region markers now use exact row-derived statistics for the selected standard window and distinguish incomplete coverage, unsupported filters, loading, errors and verified zero.

The new source is `property-b87eea7c1c03dc21`. Its private publication audit reparsed originals, verified SHA hashes, and preserved every previous transaction ID: 3,161,421 previous rows, 10,977,282 candidate rows, 0 removed IDs. This is a release snapshot, not the continuously advancing collection ledger and not a 20-year completion claim.

## Data contract
- `pipeline/real_estate_region_metrics.py` processes one region at a time from a hash-verified immutable publication; no source requests or checkpoint writes.
- Region statistics use eligible individual reports, not averages of monthly or complex medians. Equal-looking reports retain distinct IDs. Cancelled and ineligible reports are excluded by the audited source eligibility field.
- Supported presets: 1/3/6/12/36/60/120/240 months ending at the release's latest completed or provisional month; all exclusive areas or `84-band`; sale, all rent, jeonse and monthly rent.
- Other exact areas and older period end dates remain explicitly unsupported by this small map aggregation asset. Transaction detail and its filters remain available. Never substitute another month's or area's price.
- A complete window can show zero. A window with missing months is partial; a wholly unavailable source period is not zero. Complete province totals require every constituent region to be complete; observed partial counts carry an explicit partial label.
- Region anchors remain SGIS navigation anchors, not apartment locations or a historical administrative-code crosswalk.
- 256 preserved publication regions were derived without collecting outside the user's acquisition scope. Source rows: 10,977,282; transaction assets reverified: 2,704; output: 4,008,029 bytes.

Examples for 2023.10–2026.09 sale / exclusive 84–85㎡: Songpa 4,274 reports and 36/36 months; Gangnam 2,177 and 36/36; Ansan Danwon 1,951 and 36/36. Daejeon Seo, Sejong and Cheongju Sangdang have 14/36 months in this snapshot, so their observed values are explicitly partial.

## Release linkage
Official source matching was repeated for the candidate using independent district/road/building-number and name evidence. It produced 844 linked Seoul provider points and 839 official basic-information records. Point CRS/geometry accuracy is still unconfirmed; no guessed coordinate or polygon was promoted. Earlier release sources remain registered for pinned shares.

The candidate static data stage contains 13,512 files / 18,658,589,460 bytes. All 13,509 source assets are hardlinks, not additional byte-for-byte disk copies. Files remain within the existing Pages limits. Publication requires a verified immutable data URL, application pin, browser verification, CI, merge and production promotion.

## Upload reliability
Content-addressed missing/upload/retain operations retry transport interruptions at most twice with bounded backoff. HTTP authorization handling retains its existing single-refresh rule. Deployment POST is never automatically retried because its outcome can be uncertain. Confirmed buckets are retained so interrupted runs reuse already uploaded bytes. Upload lanes remain capped at two; ordinary buckets are now 4MiB instead of 16MiB to limit request size. A single larger asset retains its independently audited platform limit. Transport diagnostics expose only an allowlisted error category, not credential or request details.

## Storage and acquisition
Collection remains single-owner on the VPS, with shared daily budgets and the 30GiB reserve. The last inspected scope ledger was 32,568 / 48,048 available region-month-trade jobs; acquisition and publication are separate. Historical code changes and source-unavailable rental years remain distinct limitations.

Local retained transactions and summary publications passed 11,520 asset SHA checks. Reproducible intermediate candidates totalling 9,806,238,740 logical bytes were approved for retirement, but the tool's automatic approval review rejected deletion (`blocked by policy`), so they remain. Transparent NTFS compression of retained static JSON archives and reproducible candidates completed without deleting files. The two measured free-space deltas total 11,496,210,432 bytes; concurrent disk activity can affect these net readings. All 43,691 content files (19,311,436,242 logical bytes) passed post-compression SHA checks. Full inventory deduplicates hardlinks and measures compressed allocation separately from logical file lengths. Source checkpoints, CAS backups, dirty user checkout and other services are preserved.

## Verification status
Typecheck, lint, production build and 1,158 web tests passed. The related Python suite passed 47 tests. A full local Python run could not collect 26 modules because the minimal staging environment lacks optional mapping/scientific dependencies; full locked-environment CI is required before merge. Public browser and deployment results are recorded after candidate completion, not assumed from these checks.
## Map-marker payload correction
Before public promotion, the larger release exposed a second regression: Songpa's region-wide 36-month summaries total 19,216,951 bytes and Gangnam's 26,514,100 bytes, exceeding the existing 16MiB map query limit. The limit was not raised.

`pipeline.real_estate_map_price_presets` now extracts only condition-matched latest reports for the already linked provider-point IDs. It retains the original transaction ID, contract date, amount and exclusive area. Selection is deterministic, each preset lists at most one actual report per complex, and rent kinds remain separate. Monthly rows are never reported as an actual point position.

The 25 Seoul assets total 2,500,174 bytes, derived from 366,787 verified summary rows. Songpa is 129,636 bytes and Gangnam 156,985 bytes, each serving all supported presets. These are payload reductions, not overall application-speed measurements. Arbitrary exact areas and older period endpoints retain the existing bounded summary fallback. Regions without linked map points no longer download apartment price history just to render no apartment markers. The separate district metrics remain usable there.

After this change: typecheck, lint, 1,162 web tests and 9 focused aggregation/selection Python tests pass. Web and full pipeline CI passed on commit 970e01e. Full CI and public browser validation must pass on the final release-linked commit before production promotion.

## Previous public release compatibility
The same audited aggregation is generated for the current public release `property-8879dff1b31ac5f0`. This fixes existing pinned shares without replacing their transaction source or claiming the new candidate is published. The source publication is independently hash-checked. The previous release's missing months remain partial; a newer release's counts are never substituted.

The data candidate transfer has encountered bounded timeouts. Application verification can proceed against the existing immutable data origin while this transfer continues. Registration and promotion of `property-b87eea7c1c03dc21` remain separate gates. The application will not silently reference an unverified data origin.
