# Indicator Catalog

This catalog summarizes the indicators currently defined in `config/indicators.yaml`. The YAML registry remains the source of truth for formulas and notes.

## WHO validation

| ID | Name | Result | Formula / basis | Target |
| --- | --- | --- | --- | --- |
| VAL-01 | ANC Coverage | percentage | `AN01a / expected_pregnancies` | 95 |
| VAL-02 | HIV Testing Coverage at ANC | percentage | `AN17a / AN01a` | 95 |
| VAL-03 | ART Coverage among HIV+ Pregnant Women | percentage | `(AN20a + AN20b) / (AN18a + AN21-POS)` | 95 |
| VAL-04 | Syphilis Testing Coverage at ANC | percentage | `AN14a / AN01a` | 95 |
| VAL-05 | Syphilis Treatment Coverage | percentage | `AN14c / AN14b` | 95 |
| VAL-06 | HepB Birth Dose Coverage (Proxy) | percentage | `CL02 / MA05a1` | 90 |

## HIV cascade

| ID | Name | Result | Formula / basis |
| --- | --- | --- | --- |
| HIV-01 | Known HIV Status at ANC | percentage | `AN21 / AN01a` |
| HIV-02 | HIV Positivity Rate at ANC | percentage | `AN18a / AN17a` |
| HIV-03 | ART Initiation Rate (Newly Identified) | percentage | `AN20b / AN18a` |
| HIV-04 | Viral Load Sample Collection Rate | percentage | `AN23b / AN23a` |
| HIV-05 | Viral Load Suppression Rate | percentage | `AN23d / AN23b` |
| HIV-06 | HEI Prophylaxis Coverage | percentage | `OE03 / OE01` |
| HIV-07 | Early Infant Diagnosis by 2 Months | percentage | `OE06 / OE01` |
| HIV-08 | HEI Retention Rate | percentage | `(OE01 - OE12 - OE13 - OE14) / OE01` |
| HIV-09 | HEI Loss to Follow-Up Rate | percentage | `OE14 / OE01` |
| HIV-10 | HIV+ Delivery Rate | percentage | `MA21a / MA04` |

## HBV cascade

| ID | Name | Result | Formula / basis | Target |
| --- | --- | --- | --- | --- |
| HBV-01 | HBV Testing Coverage at ANC | percentage | `AN16a / AN01a` | |
| HBV-02 | HBV Positivity Rate | percentage | `AN16b / AN16a` | |
| HBV-05 | HBV Treatment Initiation Rate | percentage | `HB11 / HB10` | 90 |
| HBV-07 | HepB Birth Dose Coverage | percentage | alias of `VAL-06` | 90 |
| HBV-08 | Penta 3 Coverage | count | `CL12` | |

## Syphilis

| ID | Name | Result | Formula / basis |
| --- | --- | --- | --- |
| SYP-01 | Syphilis Positivity Rate | percentage | `AN14b / AN14a` |

## System

| ID | Name | Result | Formula / basis |
| --- | --- | --- | --- |
| SYS-01 | Reporting Completeness | percentage | custom `completeness_api` calculation |
| SYS-03 | Missed Appointment Rate | percentage | `033B-AP05 / 033B-AP04` |

## Supply

| ID | Name | Result | Formula / basis |
| --- | --- | --- | --- |
| SUP-01 | HBsAg Kits Consumed | count | `SS40a` |
| SUP-02 | HBsAg Stockout Days | count | `SS40b` |
| SUP-03 | HIV/Syphilis Duo Kits Consumed | count | `SS41a` |
| SUP-04 | Duo Kit Stockout Days | count | `SS41b` |
| SUP-05 | HBsAg Days of Use | days | custom `days_of_use` using `SS40c` stock on hand and `SS40a` consumption |
| SUP-06 | Duo Kit Days of Use | days | custom `days_of_use` using `SS41c` stock on hand and `SS41a` consumption |

## Notes

- Mapping codes such as `AN01a`, `SS40a`, and `033B-AP05` are resolved through `config/mappings.yaml`.
- `AN21-POS` is derived from configured positive category-option-combo mappings.
- Weekly indicators are excluded from monthly trends.
- Supply indicators are enriched further by the `app/supply/` package for validation, forecasting, and alerts.
