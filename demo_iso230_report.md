# ecmc ISO 230 Bidirectional Positioning Report

Generated: `2026-03-07 23:30:43`

## Status

- State: `Demo`
- Bidirectional accuracy: `0.00732`
- Bidirectional systematic deviation: `0.00686`
- Range of mean bidirectional positional deviation: `0.00409`
- Bidirectional repeatability: `0.00404`
- Unidirectional repeatability: `0.00098`
- Mean reversal value: `0.00277`
- Maximum reversal value: `0.00321`
- Mean bias: `-0.00062`
- Linearity residual: `0.00253`
- Linear fit slope: `1.00000`
- Linear fit intercept: `-0.00266`

## Configuration

- IOC prefix: `DEMO:ECMC`
- Axis ID: `7`
- Motor record: `DEMO:AXIS7`
- Configured reference PVs: `Ref 1=SIM:LASER:MEAS`
- Reference used for report calculations: `SIM:LASER:MEAS`
- Range: `0.00000 .. 1600.00000`
- Target generation mode: `iso-short-travel`
- Target generation rule: `ISO-style non-uniform targets: minimum five random target positions per metre`
- Base interval: `228.57143`
- Targets: `0.00000, 248.32508, 492.23062, 634.25114, 898.08078, 1098.58378, 1365.58207, 1600.00000`
- Cycles: `5`
- Display decimals: `5`
- Settle time: `1.00000 s`
- Samples per point: `5`
- Sample interval: `150 ms`
- Approach margin outside targets: `80.00000`
- Motion parameters: `VELO=25.00000 ACCL=80.00000 VMAX=40.00000 ACCS=120.00000`

## Graph

Bidirectional positioning error relative to commanded target. Forward mean error is shown in blue, reverse mean error in amber, repeatability ranges as vertical bars, and reversal value as the dashed violet segment at each target.

<svg xmlns="http://www.w3.org/2000/svg" width="980" height="520" viewBox="0 0 980 520" role="img" aria-label="Bidirectional positioning error graph"><text x="490.00" y="18.00" font-size="16" text-anchor="middle" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="700">Bidirectional Positioning Error Graph</text><text x="490.00" y="502.00" font-size="13" text-anchor="middle" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="600">Target position on axis 7</text><g transform="translate(24 238.00) rotate(-90)"><text x="0.00" y="0.00" font-size="13" text-anchor="middle" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="600">Reference error relative to commanded target</text></g><rect x="0" y="0" width="980" height="520" fill="#f8fafc" rx="12" ry="12" /><rect x="90" y="26" width="864" height="424" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.2" /><line x1="90.00" y1="450.00" x2="954.00" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="454.00" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00442</text><line x1="90.00" y1="379.33" x2="954.00" y2="379.33" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="383.33" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00301</text><line x1="90.00" y1="308.67" x2="954.00" y2="308.67" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="312.67" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00160</text><line x1="90.00" y1="238.00" x2="954.00" y2="238.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="242.00" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00019</text><line x1="90.00" y1="167.33" x2="954.00" y2="167.33" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="171.33" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00122</text><line x1="90.00" y1="96.67" x2="954.00" y2="96.67" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="100.67" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00263</text><line x1="90.00" y1="26.00" x2="954.00" y2="26.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="78.00" y="30.00" font-size="12" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00404</text><line x1="90.00" y1="26.00" x2="90.00" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="90.00" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00000</text><line x1="224.10" y1="26.00" x2="224.10" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="224.10" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">248.32508</text><line x1="355.80" y1="26.00" x2="355.80" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="355.80" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">492.23062</text><line x1="432.50" y1="26.00" x2="432.50" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="432.50" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">634.25114</text><line x1="574.96" y1="26.00" x2="574.96" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="574.96" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">898.08078</text><line x1="683.24" y1="26.00" x2="683.24" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="683.24" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">1098.58378</text><line x1="827.41" y1="26.00" x2="827.41" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="827.41" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">1365.58207</text><line x1="954.00" y1="26.00" x2="954.00" y2="450.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="954.00" y="474.00" font-size="12" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">1600.00000</text><line x1="90.00" y1="228.48" x2="954.00" y2="228.48" stroke="#0f172a" stroke-width="1.70" /><text x="950.00" y="220.48" font-size="11" text-anchor="end" fill="#0f172a" font-family="Helvetica, Arial, sans-serif" font-weight="600">zero error</text><line x1="82.00" y1="395.93" x2="82.00" y2="346.75" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="98.00" y1="275.88" x2="98.00" y2="254.54" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="90.00" y1="373.62" x2="90.00" y2="265.25" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="216.10" y1="420.76" x2="216.10" y2="378.00" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="232.10" y1="270.62" x2="232.10" y2="257.81" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="224.10" y1="397.94" x2="224.10" y2="265.33" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="347.80" y1="401.34" x2="347.80" y2="385.64" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="363.80" y1="251.48" x2="363.80" y2="241.98" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="355.80" y1="392.85" x2="355.80" y2="245.31" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="424.50" y1="406.85" x2="424.50" y2="365.14" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="440.50" y1="239.62" x2="440.50" y2="197.77" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="432.50" y1="381.20" x2="432.50" y2="220.60" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="566.96" y1="367.89" x2="566.96" y2="326.41" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="582.96" y1="209.27" x2="582.96" y2="188.97" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="574.96" y1="352.42" x2="574.96" y2="196.65" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="675.24" y1="322.52" x2="675.24" y2="287.27" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="691.24" y1="164.05" x2="691.24" y2="133.43" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="683.24" y1="306.66" x2="683.24" y2="151.07" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="819.41" y1="258.07" x2="819.41" y2="229.68" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="835.41" y1="132.34" x2="835.41" y2="94.86" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="827.41" y1="241.50" x2="827.41" y2="107.45" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="946.00" y1="199.75" x2="946.00" y2="161.64" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="962.00" y1="88.63" x2="962.00" y2="55.24" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="954.00" y1="184.41" x2="954.00" y2="69.44" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><polyline fill="none" stroke="#1d4ed8" stroke-width="2.8" points="82.00,373.62 216.10,397.94 347.80,392.85 424.50,381.20 566.96,352.42 675.24,306.66 819.41,241.50 946.00,184.41" /><polyline fill="none" stroke="#d97706" stroke-width="2.8" points="98.00,265.25 232.10,265.33 363.80,245.31 440.50,220.60 582.96,196.65 691.24,151.07 835.41,107.45 962.00,69.44" /><circle cx="82.00" cy="373.62" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="216.10" cy="397.94" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="347.80" cy="392.85" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="424.50" cy="381.20" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="566.96" cy="352.42" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="675.24" cy="306.66" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="819.41" cy="241.50" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="946.00" cy="184.41" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><rect x="93.50" y="260.75" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="227.60" y="260.83" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="359.30" y="240.81" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="436.00" y="216.10" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="578.46" y="192.15" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="686.74" y="146.57" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="830.91" y="102.95" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="957.50" y="64.94" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><line x1="100.00" y1="40.00" x2="122.00" y2="40.00" stroke="#1d4ed8" stroke-width="3.00" /><circle cx="111.00" cy="40.00" r="4.80" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><text x="130.00" y="44.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">Forward mean error</text><line x1="310.00" y1="40.00" x2="332.00" y2="40.00" stroke="#d97706" stroke-width="3.00" /><rect x="316.50" y="35.50" width="9.00" height="9.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><text x="340.00" y="44.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">Reverse mean error</text><line x1="560.00" y1="34.00" x2="560.00" y2="52.00" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" /><text x="572.00" y="44.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">Reversal gap</text><rect x="690" y="40" width="244" height="92" fill="#ffffff" stroke="#94a3b8" stroke-width="1.2" rx="8" ry="8" /><text x="704.00" y="64.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="700">Summary metrics</text><text x="704.00" y="88.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">BiDir accuracy: 0.00732</text><text x="704.00" y="108.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">BiDir systematic: 0.00686</text><text x="704.00" y="128.00" font-size="12" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">BiDir repeatability: 0.00404</text></svg>

## Notes

- This workflow uses ISO 230-style bidirectional positioning terminology derived from the supplied reference document and is not presented as certified ISO 230-2 compliance evidence.
- Mean bidirectional positional deviation is calculated as the average of the forward and reverse mean reference errors at each target.
- Reversal value is calculated as the absolute difference between forward and reverse mean reference errors at each target.
- Bidirectional repeatability is calculated from the combined forward and reverse error band at each target.

## Per-Target Results

| Target | Mean BiDir Dev | Reversal | Uni Repeat | BiDir Repeat | Fwd Mean Err | Rev Mean Err | Max Abs Err |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.00000 | -0.00182 | 0.00216 | 0.00098 | 0.00287 | -0.00290 | -0.00073 | 0.00334 |
| 248.32508 | -0.00206 | 0.00265 | 0.00085 | 0.00320 | -0.00338 | -0.00074 | 0.00384 |
| 492.23062 | -0.00181 | 0.00295 | 0.00031 | 0.00320 | -0.00328 | -0.00034 | 0.00345 |
| 634.25114 | -0.00145 | 0.00321 | 0.00084 | 0.00404 | -0.00305 | 0.00016 | 0.00356 |
| 898.08078 | -0.00092 | 0.00311 | 0.00083 | 0.00373 | -0.00247 | 0.00064 | 0.00278 |
| 1098.58378 | -0.00001 | 0.00311 | 0.00070 | 0.00376 | -0.00156 | 0.00155 | 0.00190 |
| 1365.58207 | 0.00108 | 0.00268 | 0.00075 | 0.00333 | -0.00026 | 0.00242 | 0.00267 |
| 1600.00000 | 0.00203 | 0.00230 | 0.00076 | 0.00301 | 0.00088 | 0.00318 | 0.00346 |

## Raw Measured Points

Selected reference for report/error columns: `SIM:LASER:MEAS`

| Cycle | Direction | Target | Ref Mean | Ref Std | RBV Mean | RBV Std | Ref Err | RBV Err | Timestamp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | forward | 0.00000 | -0.00294 | 0.00021 | -0.00364 | 0.00032 | -0.00294 | -0.00364 | 2026-03-07 23:30:43 |
| 1 | forward | 248.32508 | 248.32142 | 0.00022 | 248.32165 | 0.00030 | -0.00366 | -0.00343 | 2026-03-07 23:30:46 |
| 1 | forward | 492.23062 | 492.22717 | 0.00020 | 492.22700 | 0.00036 | -0.00345 | -0.00362 | 2026-03-07 23:30:49 |
| 1 | forward | 634.25114 | 634.24758 | 0.00021 | 634.24788 | 0.00026 | -0.00356 | -0.00326 | 2026-03-07 23:30:52 |
| 1 | forward | 898.08078 | 898.07829 | 0.00018 | 898.07813 | 0.00029 | -0.00249 | -0.00265 | 2026-03-07 23:30:55 |
| 1 | forward | 1098.58378 | 1098.58190 | 0.00025 | 1098.58199 | 0.00025 | -0.00188 | -0.00179 | 2026-03-07 23:30:58 |
| 1 | forward | 1365.58207 | 1365.58153 | 0.00020 | 1365.58188 | 0.00027 | -0.00054 | -0.00020 | 2026-03-07 23:31:01 |
| 1 | forward | 1600.00000 | 1600.00058 | 0.00023 | 1600.00068 | 0.00030 | 0.00058 | 0.00068 | 2026-03-07 23:31:04 |
| 1 | reverse | 1600.00000 | 1600.00279 | 0.00025 | 1600.00312 | 0.00028 | 0.00279 | 0.00312 | 2026-03-07 23:31:07 |
| 1 | reverse | 1365.58207 | 1365.58433 | 0.00021 | 1365.58476 | 0.00024 | 0.00226 | 0.00268 | 2026-03-07 23:31:10 |
| 1 | reverse | 1098.58378 | 1098.58507 | 0.00021 | 1098.58609 | 0.00036 | 0.00129 | 0.00231 | 2026-03-07 23:31:13 |
| 1 | reverse | 898.08078 | 898.08117 | 0.00026 | 898.08124 | 0.00038 | 0.00038 | 0.00046 | 2026-03-07 23:31:16 |
| 1 | reverse | 634.25114 | 634.25092 | 0.00030 | 634.25091 | 0.00030 | -0.00022 | -0.00024 | 2026-03-07 23:31:19 |
| 1 | reverse | 492.23062 | 492.23016 | 0.00019 | 492.23065 | 0.00036 | -0.00046 | 0.00003 | 2026-03-07 23:31:22 |
| 1 | reverse | 248.32508 | 248.32426 | 0.00019 | 248.32390 | 0.00024 | -0.00081 | -0.00118 | 2026-03-07 23:31:25 |
| 1 | reverse | 0.00000 | -0.00092 | 0.00023 | -0.00077 | 0.00025 | -0.00092 | -0.00077 | 2026-03-07 23:31:28 |
| 2 | forward | 0.00000 | -0.00334 | 0.00020 | -0.00407 | 0.00034 | -0.00334 | -0.00407 | 2026-03-07 23:31:31 |
| 2 | forward | 248.32508 | 248.32124 | 0.00022 | 248.32096 | 0.00031 | -0.00384 | -0.00412 | 2026-03-07 23:31:34 |
| 2 | forward | 492.23062 | 492.22746 | 0.00021 | 492.22784 | 0.00026 | -0.00316 | -0.00278 | 2026-03-07 23:31:37 |
| 2 | forward | 634.25114 | 634.24808 | 0.00025 | 634.24796 | 0.00030 | -0.00306 | -0.00318 | 2026-03-07 23:31:40 |
| 2 | forward | 898.08078 | 898.07822 | 0.00024 | 898.07896 | 0.00023 | -0.00257 | -0.00183 | 2026-03-07 23:31:43 |
| 2 | forward | 1098.58378 | 1098.58211 | 0.00020 | 1098.58206 | 0.00029 | -0.00167 | -0.00172 | 2026-03-07 23:31:46 |
| 2 | forward | 1365.58207 | 1365.58199 | 0.00022 | 1365.58172 | 0.00026 | -0.00008 | -0.00035 | 2026-03-07 23:31:49 |
| 2 | forward | 1600.00000 | 1600.00057 | 0.00024 | 1600.00031 | 0.00027 | 0.00057 | 0.00031 | 2026-03-07 23:31:52 |
| 2 | reverse | 1600.00000 | 1600.00316 | 0.00026 | 1600.00351 | 0.00036 | 0.00316 | 0.00351 | 2026-03-07 23:31:55 |
| 2 | reverse | 1365.58207 | 1365.58399 | 0.00019 | 1365.58441 | 0.00036 | 0.00192 | 0.00234 | 2026-03-07 23:31:58 |
| 2 | reverse | 1098.58378 | 1098.58511 | 0.00025 | 1098.58598 | 0.00025 | 0.00133 | 0.00220 | 2026-03-07 23:32:01 |
| 2 | reverse | 898.08078 | 898.08144 | 0.00026 | 898.08164 | 0.00026 | 0.00066 | 0.00086 | 2026-03-07 23:32:04 |
| 2 | reverse | 634.25114 | 634.25130 | 0.00020 | 634.25097 | 0.00033 | 0.00016 | -0.00017 | 2026-03-07 23:32:07 |
| 2 | reverse | 492.23062 | 492.23033 | 0.00018 | 492.23028 | 0.00023 | -0.00029 | -0.00034 | 2026-03-07 23:32:10 |
| 2 | reverse | 248.32508 | 248.32424 | 0.00028 | 248.32410 | 0.00027 | -0.00084 | -0.00097 | 2026-03-07 23:32:13 |
| 2 | reverse | 0.00000 | -0.00065 | 0.00023 | -0.00044 | 0.00023 | -0.00065 | -0.00044 | 2026-03-07 23:32:16 |
| 3 | forward | 0.00000 | -0.00295 | 0.00023 | -0.00282 | 0.00034 | -0.00295 | -0.00282 | 2026-03-07 23:32:19 |
| 3 | forward | 248.32508 | 248.32197 | 0.00030 | 248.32209 | 0.00028 | -0.00311 | -0.00298 | 2026-03-07 23:32:22 |
| 3 | forward | 492.23062 | 492.22748 | 0.00018 | 492.22703 | 0.00023 | -0.00314 | -0.00359 | 2026-03-07 23:32:25 |
| 3 | forward | 634.25114 | 634.24826 | 0.00025 | 634.24885 | 0.00033 | -0.00288 | -0.00229 | 2026-03-07 23:32:28 |
| 3 | forward | 898.08078 | 898.07820 | 0.00027 | 898.07844 | 0.00024 | -0.00258 | -0.00234 | 2026-03-07 23:32:31 |
| 3 | forward | 1098.58378 | 1098.58230 | 0.00026 | 1098.58255 | 0.00027 | -0.00148 | -0.00123 | 2026-03-07 23:32:34 |
| 3 | forward | 1365.58207 | 1365.58148 | 0.00019 | 1365.58169 | 0.00027 | -0.00059 | -0.00039 | 2026-03-07 23:32:37 |
| 3 | forward | 1600.00000 | 1600.00100 | 0.00024 | 1600.00165 | 0.00037 | 0.00100 | 0.00165 | 2026-03-07 23:32:40 |
| 3 | reverse | 1600.00000 | 1600.00331 | 0.00018 | 1600.00351 | 0.00024 | 0.00331 | 0.00351 | 2026-03-07 23:32:43 |
| 3 | reverse | 1365.58207 | 1365.58467 | 0.00019 | 1365.58465 | 0.00024 | 0.00260 | 0.00257 | 2026-03-07 23:32:46 |
| 3 | reverse | 1098.58378 | 1098.58519 | 0.00019 | 1098.58540 | 0.00028 | 0.00141 | 0.00162 | 2026-03-07 23:32:49 |
| 3 | reverse | 898.08078 | 898.08137 | 0.00028 | 898.08135 | 0.00027 | 0.00058 | 0.00057 | 2026-03-07 23:32:52 |
| 3 | reverse | 634.25114 | 634.25113 | 0.00026 | 634.25135 | 0.00025 | -0.00002 | 0.00020 | 2026-03-07 23:32:55 |
| 3 | reverse | 492.23062 | 492.23025 | 0.00021 | 492.22981 | 0.00030 | -0.00037 | -0.00081 | 2026-03-07 23:32:58 |
| 3 | reverse | 248.32508 | 248.32449 | 0.00024 | 248.32380 | 0.00031 | -0.00059 | -0.00128 | 2026-03-07 23:33:01 |
| 3 | reverse | 0.00000 | -0.00095 | 0.00019 | -0.00130 | 0.00025 | -0.00095 | -0.00130 | 2026-03-07 23:33:04 |
| 4 | forward | 0.00000 | -0.00289 | 0.00023 | -0.00352 | 0.00033 | -0.00289 | -0.00352 | 2026-03-07 23:33:07 |
| 4 | forward | 248.32508 | 248.32176 | 0.00019 | 248.32148 | 0.00026 | -0.00332 | -0.00360 | 2026-03-07 23:33:10 |
| 4 | forward | 492.23062 | 492.22733 | 0.00023 | 492.22729 | 0.00035 | -0.00330 | -0.00333 | 2026-03-07 23:33:13 |
| 4 | forward | 634.25114 | 634.24842 | 0.00023 | 634.24861 | 0.00026 | -0.00273 | -0.00254 | 2026-03-07 23:33:16 |
| 4 | forward | 898.08078 | 898.07800 | 0.00019 | 898.07815 | 0.00025 | -0.00278 | -0.00263 | 2026-03-07 23:33:19 |
| 4 | forward | 1098.58378 | 1098.58217 | 0.00023 | 1098.58256 | 0.00023 | -0.00161 | -0.00122 | 2026-03-07 23:33:22 |
| 4 | forward | 1365.58207 | 1365.58201 | 0.00029 | 1365.58182 | 0.00031 | -0.00006 | -0.00025 | 2026-03-07 23:33:25 |
| 4 | forward | 1600.00000 | 1600.00133 | 0.00019 | 1600.00203 | 0.00027 | 0.00133 | 0.00203 | 2026-03-07 23:33:28 |
| 4 | reverse | 1600.00000 | 1600.00316 | 0.00022 | 1600.00323 | 0.00029 | 0.00316 | 0.00323 | 2026-03-07 23:33:31 |
| 4 | reverse | 1365.58207 | 1365.58474 | 0.00021 | 1365.58481 | 0.00038 | 0.00267 | 0.00273 | 2026-03-07 23:33:34 |
| 4 | reverse | 1098.58378 | 1098.58568 | 0.00026 | 1098.58611 | 0.00024 | 0.00190 | 0.00233 | 2026-03-07 23:33:37 |
| 4 | reverse | 898.08078 | 898.08155 | 0.00024 | 898.08111 | 0.00034 | 0.00076 | 0.00033 | 2026-03-07 23:33:40 |
| 4 | reverse | 634.25114 | 634.25140 | 0.00026 | 634.25136 | 0.00023 | 0.00026 | 0.00022 | 2026-03-07 23:33:43 |
| 4 | reverse | 492.23062 | 492.23034 | 0.00020 | 492.23093 | 0.00027 | -0.00028 | 0.00030 | 2026-03-07 23:33:46 |
| 4 | reverse | 248.32508 | 248.32437 | 0.00018 | 248.32411 | 0.00029 | -0.00071 | -0.00097 | 2026-03-07 23:33:49 |
| 4 | reverse | 0.00000 | -0.00064 | 0.00018 | -0.00070 | 0.00036 | -0.00064 | -0.00070 | 2026-03-07 23:33:52 |
| 5 | forward | 0.00000 | -0.00236 | 0.00021 | -0.00235 | 0.00026 | -0.00236 | -0.00235 | 2026-03-07 23:33:55 |
| 5 | forward | 248.32508 | 248.32209 | 0.00021 | 248.32215 | 0.00023 | -0.00299 | -0.00293 | 2026-03-07 23:33:58 |
| 5 | forward | 492.23062 | 492.22726 | 0.00022 | 492.22741 | 0.00025 | -0.00336 | -0.00321 | 2026-03-07 23:34:01 |
| 5 | forward | 634.25114 | 634.24814 | 0.00019 | 634.24860 | 0.00033 | -0.00301 | -0.00254 | 2026-03-07 23:34:04 |
| 5 | forward | 898.08078 | 898.07883 | 0.00019 | 898.07880 | 0.00025 | -0.00196 | -0.00198 | 2026-03-07 23:34:07 |
| 5 | forward | 1098.58378 | 1098.58261 | 0.00018 | 1098.58212 | 0.00038 | -0.00117 | -0.00166 | 2026-03-07 23:34:10 |
| 5 | forward | 1365.58207 | 1365.58205 | 0.00022 | 1365.58252 | 0.00032 | -0.00002 | 0.00045 | 2026-03-07 23:34:13 |
| 5 | forward | 1600.00000 | 1600.00091 | 0.00024 | 1600.00086 | 0.00026 | 0.00091 | 0.00086 | 2026-03-07 23:34:16 |
| 5 | reverse | 1600.00000 | 1600.00346 | 0.00018 | 1600.00361 | 0.00026 | 0.00346 | 0.00361 | 2026-03-07 23:34:19 |
| 5 | reverse | 1365.58207 | 1365.58471 | 0.00021 | 1365.58509 | 0.00024 | 0.00263 | 0.00301 | 2026-03-07 23:34:22 |
| 5 | reverse | 1098.58378 | 1098.58559 | 0.00024 | 1098.58532 | 0.00034 | 0.00181 | 0.00154 | 2026-03-07 23:34:25 |
| 5 | reverse | 898.08078 | 898.08157 | 0.00019 | 898.08175 | 0.00026 | 0.00079 | 0.00097 | 2026-03-07 23:34:28 |
| 5 | reverse | 634.25114 | 634.25176 | 0.00023 | 634.25120 | 0.00026 | 0.00061 | 0.00006 | 2026-03-07 23:34:31 |
| 5 | reverse | 492.23062 | 492.23035 | 0.00019 | 492.23037 | 0.00024 | -0.00027 | -0.00025 | 2026-03-07 23:34:34 |
| 5 | reverse | 248.32508 | 248.32435 | 0.00018 | 248.32399 | 0.00028 | -0.00073 | -0.00108 | 2026-03-07 23:34:37 |
| 5 | reverse | 0.00000 | -0.00052 | 0.00018 | -0.00061 | 0.00033 | -0.00052 | -0.00061 | 2026-03-07 23:34:40 |
