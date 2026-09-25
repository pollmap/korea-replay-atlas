# Selected administrative region geometry

The 2D selection overlay uses actual SGIS polygons for a selected province,
district, or administrative dong. It does not use camera bounding rectangles as
area boundaries. The source is the [SGIS administrative boundary archive](https://www.data.go.kr/data/15129688/fileData.do),
dated **2025-06-30**, with SHA-256
`f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6`.

## Identity and scope

- 17 provinces + 252 SGIS city/district records; 3,559 administrative dongs.
- Region selections resolve exact full province/district names. A legal MOLIT
  code is never treated as an SGIS code. For example, Seodaemun is SGIS `11130`,
  not its MOLIT regional identifier `11410`.
- Administrative dongs belong to their source SGIS five-digit parent. They must
  be labelled **행정동**. These polygons do not represent legal dongs.
- An unknown full name or a different boundary reference date returns no
  geometry. A newly reorganized area is not silently replaced by an old area.
- This is a selected-area display overlay, not a crosswalk, area calculation,
  point assignment, or transaction aggregation dataset.

## Geometry verification

The original projected EPSG:5179 polygons are validated by the existing SGIS
reader. Simplification occurs in source metres with topology preservation and
a conservative per-ring displacement bound. When the bound fails, the generator
retries lower tolerances and finally retains the exact source. Every polygon
component and hole is required to remain. Coordinates are projected to WGS84
and encoded at seven decimal digits, increasing to eight or nine if required to
retain valid rings. Paired vertex displacement after inverse projection bounds
the quantization error.

| Scope | Source vertices | Display vertices | Preserved components | Holes | Maximum display error |
|---|---:|---:|---:|---:|---:|
| Province/district | 11,520,397 | 700,111 | 27,666 | 21 | 20.006925 m |
| Administrative dong | 8,421,798 | 685,981 | 17,214 | 7 | 20.006910 m |

The 20 m simplification is intended for selection feedback. It does not promise
survey accuracy. Adjacent display polygons are simplified individually; they
must not be used as an edge-matched analytical coverage. Ulleung/Dokdo and
offshore island components are retained and checked.

## Loading and memory

`src/region-selection.ts` exports asynchronous helpers:

```ts
selectedRegionBoundary(fullRegionName, boundaryReferenceDate, signal?)
regionAdministrativeDongs(fullRegionName, boundaryReferenceDate, signal?)
selectedSgisBoundary(sgisCode, boundaryReferenceDate, signal?)
administrativeDongBoundary(sgisCode, boundaryReferenceDate, signal?)
clearRegionBoundaryCache()
```

The two initial indexes total 53,446 bytes. Geometry is provided as 521 lazy URL
assets, totaling 9,029,748 bytes; it is not embedded in the initial JavaScript.
One province/district needs one asset. A selected district's dongs need one
additional asset. The largest asset is 718,226 bytes, below the 1 MiB hard limit.

Every response is checked against the source manifest byte length and SHA-256
before parsing. Failed responses are not cached. The LRU stores at most 4 MiB of
verified compact bytes; it does not retain nationwide decoded GeoJSON. Consumers
must abort superseded requests, ignore stale completions, and retain their prior
visible shape until replacement data is ready. Unmount can clear the compact
cache. The shared service-worker download gate also needs to manage these asset
paths.

## Reproduction

The original archive is private/local and is not added to the code repository.
From the repository root:

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pipeline.region_selection --archive .local/admin-boundaries-20260920/raw/sgis-20250630.zip --output src/data/region-selection.json --report .local/region-selection-20260926/audit.json
.venv/Scripts/python.exe -X utf8 -B -m pipeline.region_selection --archive .local/admin-boundaries-20260920/raw/sgis-20250630.zip --output src/data/region-selection-dongs.json --report .local/region-selection-20260926/dong-audit.json --dongs
```

Focused validation checks full chunk manifests, all closed rings, coordinate
ranges, island retention, administrative/ legal identity separation, stale
source rejection, integrity failures, cancellation and cache bounds. Browser
selection appearance and zoom behaviour remain integration checks.
