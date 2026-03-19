# ecmc ISO 230 Bidirectional Positioning Report

Generated: `2026-03-19 10:11:20`

## Status

- State: `Complete`
- Bidirectional accuracy: `0.01504`
- Bidirectional systematic deviation: `0.01368`
- Range of mean bidirectional positional deviation: `0.01063`
- Bidirectional repeatability: `0.00362`
- Unidirectional repeatability: `0.00190`
- Mean reversal value: `-0.00290`
- Maximum reversal value: `0.00352`
- Mean bias: `-0.00008`
- Linearity residual: `0.00862`
- Linear fit slope: `1.00025`
- Linear fit intercept: `-0.00010`

## Configuration

- IOC prefix: `c6025a-08`
- Axis ID: `5`
- Motor record: `c6025a-08:TR_LO`
- Configured reference PVs: `Ref 1=c6025a-08:TR_LO-Enc02-PosAct`
- Reference used for report calculations: `c6025a-08:TR_LO-Enc02-PosAct`
- Reference gain: `1.00000`
- Reference offset: `0.00000`
- Range: `-5.00000 .. 5.00000`
- Target generation mode: `iso-short-travel`
- Target generation rule: `ISO-style non-uniform targets: minimum five random target positions per metre`
- Base interval: `1.42857`
- Targets: `-5.00000, -3.54578, -1.78508, -0.66663, 0.87005, 2.40306, 3.39152, 5.00000`
- Cycles: `5`
- Display decimals: `5`
- Settle time: `0.00000 s`
- Samples per point: `2`
- Sample interval: `150 ms`
- Approach margin outside targets: `0.50000`
- Motion parameters: `VELO=2.00000 ACCL=0.30000 VMAX=5.00000 ACCS=1.00000`

## Graph

Bidirectional positioning error relative to commanded target. Forward mean error is shown in blue, reverse mean error in amber, ISO repeatability intervals are shown as vertical bars, and the reversal value is the dashed violet segment at each target.

<svg xmlns="http://www.w3.org/2000/svg" width="760" height="340" viewBox="0 0 760 340" role="img" aria-label="Bidirectional positioning error graph"><text x="380.00" y="18.00" font-size="13" text-anchor="middle" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="700">Bidirectional Positioning Error Graph</text><text x="380.00" y="322.00" font-size="11" text-anchor="middle" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="600">Target position on axis 5</text><g transform="translate(24 153.00) rotate(-90)"><text x="0.00" y="0.00" font-size="11" text-anchor="middle" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="600">Reference error relative to commanded target</text></g><rect x="0" y="0" width="760" height="340" fill="#f8fafc" rx="12" ry="12" /><rect x="68" y="18" width="674" height="270" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.2" /><line x1="68.00" y1="288.00" x2="742.00" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="292.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00660</text><line x1="68.00" y1="243.00" x2="742.00" y2="243.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="247.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00369</text><line x1="68.00" y1="198.00" x2="742.00" y2="198.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="202.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.00079</text><line x1="68.00" y1="153.00" x2="742.00" y2="153.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="157.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00212</text><line x1="68.00" y1="108.00" x2="742.00" y2="108.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="112.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00503</text><line x1="68.00" y1="63.00" x2="742.00" y2="63.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="67.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.00794</text><line x1="68.00" y1="18.00" x2="742.00" y2="18.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.850" /><text x="56.00" y="22.00" font-size="10" text-anchor="end" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.01085</text><line x1="68.00" y1="18.00" x2="68.00" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="68.00" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-5.00000</text><line x1="166.01" y1="18.00" x2="166.01" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="166.01" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-3.54578</text><line x1="284.69" y1="18.00" x2="284.69" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="284.69" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-1.78508</text><line x1="360.07" y1="18.00" x2="360.07" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="360.07" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">-0.66663</text><line x1="463.64" y1="18.00" x2="463.64" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="463.64" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">0.87005</text><line x1="566.97" y1="18.00" x2="566.97" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="566.97" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">2.40306</text><line x1="633.59" y1="18.00" x2="633.59" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="633.59" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">3.39152</text><line x1="742.00" y1="18.00" x2="742.00" y2="288.00" stroke="#cbd5e1" stroke-width="1.00" stroke-dasharray="4 5" opacity="0.600" /><text x="742.00" y="308.00" font-size="10" text-anchor="middle" fill="#334155" font-family="Helvetica, Arial, sans-serif" font-weight="400">5.00000</text><line x1="68.00" y1="185.84" x2="742.00" y2="185.84" stroke="#0f172a" stroke-width="1.70" /><text x="738.00" y="177.84" font-size="10" text-anchor="end" fill="#0f172a" font-family="Helvetica, Arial, sans-serif" font-weight="600">zero error</text><line x1="60.00" y1="242.68" x2="60.00" y2="213.62" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="76.00" y1="204.08" x2="76.00" y2="182.71" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="68.00" y1="228.15" x2="68.00" y2="193.40" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="158.01" y1="228.71" x2="158.01" y2="211.48" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="174.01" y1="185.30" x2="174.01" y2="171.79" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="166.01" y1="220.10" x2="166.01" y2="178.54" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="276.69" y1="209.54" x2="276.69" y2="196.90" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="292.69" y1="169.92" x2="292.69" y2="145.09" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="284.69" y1="203.22" x2="284.69" y2="157.51" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="352.07" y1="199.67" x2="352.07" y2="186.15" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="368.07" y1="158.11" x2="368.07" y2="144.60" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="360.07" y1="192.91" x2="360.07" y2="151.36" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="455.64" y1="269.38" x2="455.64" y2="249.68" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="471.64" y1="220.21" x2="471.64" y2="203.65" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="463.64" y1="259.53" x2="463.64" y2="211.93" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="558.97" y1="253.12" x2="558.97" y2="240.48" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="574.97" y1="211.40" x2="574.97" y2="190.03" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="566.97" y1="246.80" x2="566.97" y2="200.71" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="625.59" y1="98.05" x2="625.59" y2="91.29" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="641.59" y1="59.03" x2="641.59" y2="36.62" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="633.59" y1="94.67" x2="633.59" y2="47.83" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><line x1="734.00" y1="242.83" x2="734.00" y2="218.00" stroke="#93c5fd" stroke-width="8.00" opacity="0.900" /><line x1="750.00" y1="190.75" x2="750.00" y2="161.29" stroke="#fcd34d" stroke-width="8.00" opacity="0.900" /><line x1="742.00" y1="230.42" x2="742.00" y2="176.02" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" opacity="0.900" /><polyline fill="none" stroke="#1d4ed8" stroke-width="2.2" points="60.00,228.15 158.01,220.10 276.69,203.22 352.07,192.91 455.64,259.53 558.97,246.80 625.59,94.67 734.00,230.42" /><polyline fill="none" stroke="#d97706" stroke-width="2.2" points="76.00,193.40 174.01,178.54 292.69,157.51 368.07,151.36 471.64,211.93 574.97,200.71 641.59,47.83 750.00,176.02" /><circle cx="60.00" cy="228.15" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="158.01" cy="220.10" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="276.69" cy="203.22" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="352.07" cy="192.91" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="455.64" cy="259.53" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="558.97" cy="246.80" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="625.59" cy="94.67" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><circle cx="734.00" cy="230.42" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><rect x="72.50" y="189.90" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="170.51" y="175.04" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="289.19" y="154.01" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="364.57" y="147.86" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="468.14" y="208.43" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="571.47" y="197.21" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="638.09" y="44.33" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><rect x="746.50" y="172.52" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><line x1="78.00" y1="32.00" x2="100.00" y2="32.00" stroke="#1d4ed8" stroke-width="3.00" /><circle cx="89.00" cy="32.00" r="3.60" fill="#1d4ed8" stroke="#ffffff" stroke-width="1.50" /><text x="108.00" y="36.00" font-size="10" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">Forward mean error</text><line x1="288.00" y1="32.00" x2="310.00" y2="32.00" stroke="#d97706" stroke-width="3.00" /><rect x="295.50" y="28.50" width="7.00" height="7.00" fill="#d97706" stroke="#ffffff" stroke-width="1.50" rx="1.2" ry="1.2" /><text x="318.00" y="36.00" font-size="10" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">Reverse mean error</text><line x1="538.00" y1="26.00" x2="538.00" y2="44.00" stroke="#7c3aed" stroke-width="2.00" stroke-dasharray="5 4" /><text x="550.00" y="36.00" font-size="10" text-anchor="start" fill="#1f2937" font-family="Helvetica, Arial, sans-serif" font-weight="400">Reversal gap</text></svg>

## Notes

- This workflow uses ISO 230-style bidirectional positioning terminology derived from the supplied reference document and is not presented as certified ISO 230-2 compliance evidence.
- Mean bidirectional positional deviation is calculated as the average of the forward and reverse mean reference errors at each target.
- Reversal value at a target is calculated as forward mean error minus reverse mean error; the axis reversal value is the maximum absolute reversal over all targets.
- Unidirectional repeatability is calculated as 4 times the sample standard deviation for each direction at each target.
- Bidirectional repeatability is calculated as max(sqrt(2*s_f^2 + 2*s_r^2 + B_i^2), R_i^+, R_i^-).

## Per-Target Results

| Target | Mean BiDir Dev | Reversal | Uni Repeat | BiDir Repeat | Fwd Mean Err | Rev Mean Err | Max Abs Err |
| --- | --- | --- | --- | --- | --- | --- | --- |
| -5.00000 | -0.00161 | -0.00225 | 0.00188 | 0.00239 | -0.00273 | -0.00049 | 0.00342 |
| -3.54578 | -0.00087 | -0.00269 | 0.00111 | 0.00273 | -0.00221 | 0.00047 | 0.00256 |
| -1.78508 | 0.00035 | -0.00295 | 0.00160 | 0.00302 | -0.00112 | 0.00183 | 0.00212 |
| -0.66663 | 0.00089 | -0.00269 | 0.00087 | 0.00272 | -0.00046 | 0.00223 | 0.00233 |
| 0.87005 | -0.00322 | -0.00308 | 0.00127 | 0.00313 | -0.00476 | -0.00169 | 0.00506 |
| 2.40306 | -0.00245 | -0.00298 | 0.00138 | 0.00303 | -0.00394 | -0.00096 | 0.00413 |
| 3.39152 | 0.00741 | -0.00303 | 0.00145 | 0.00307 | 0.00589 | 0.00892 | 0.00936 |
| 5.00000 | -0.00112 | -0.00352 | 0.00190 | 0.00362 | -0.00288 | 0.00063 | 0.00317 |

## Operator Comments

Open loop, RLS as reference

## Raw Measured Points

Selected reference for report/error columns: `c6025a-08:TR_LO-Enc02-PosAct`
The raw reference values below are saved without applying the configured reference gain/offset.

| Cycle | Direction | Target | Raw Ref Mean | Raw Ref Std | RBV Mean | RBV Std | Raw Ref Err | RBV Err | Timestamp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | forward | -5.00000 | -5.00342 | 0.00000 | -5.00000 | 0.00000 | -0.00342 | 0.00000 | 2026-03-19 10:01:56 |
| 1 | forward | -3.54578 | -3.54761 | 0.00000 | -3.54578 | 0.00000 | -0.00182 | 0.00000 | 2026-03-19 10:02:00 |
| 1 | forward | -1.78508 | -1.78613 | 0.00000 | -1.78508 | 0.00000 | -0.00105 | 0.00000 | 2026-03-19 10:02:05 |
| 1 | forward | -0.66663 | -0.66724 | 0.00000 | -0.66664 | 0.00000 | -0.00060 | -0.00001 | 2026-03-19 10:02:09 |
| 1 | forward | 0.87005 | 0.86548 | 0.00000 | 0.87008 | 0.00000 | -0.00457 | 0.00003 | 2026-03-19 10:02:14 |
| 1 | forward | 2.40306 | 2.39917 | 0.00000 | 2.40305 | 0.00000 | -0.00389 | -0.00001 | 2026-03-19 10:02:18 |
| 1 | forward | 3.39152 | 3.39722 | 0.00000 | 3.39156 | 0.00000 | 0.00570 | 0.00004 | 2026-03-19 10:02:22 |
| 1 | forward | 5.00000 | 4.99707 | 0.00000 | 5.00000 | 0.00000 | -0.00293 | 0.00000 | 2026-03-19 10:02:27 |
| 1 | reverse | 5.00000 | 5.00098 | 0.00000 | 5.00000 | 0.00000 | 0.00098 | 0.00000 | 2026-03-19 10:02:34 |
| 1 | reverse | 3.39152 | 3.39990 | 0.00000 | 3.39156 | 0.00000 | 0.00838 | 0.00004 | 2026-03-19 10:02:38 |
| 1 | reverse | 2.40306 | 2.40210 | 0.00000 | 2.40305 | 0.00000 | -0.00096 | -0.00001 | 2026-03-19 10:02:42 |
| 1 | reverse | 0.87005 | 0.86865 | 0.00000 | 0.87008 | 0.00000 | -0.00139 | 0.00003 | 2026-03-19 10:02:47 |
| 1 | reverse | -0.66663 | -0.66431 | 0.00000 | -0.66664 | 0.00000 | 0.00233 | -0.00001 | 2026-03-19 10:02:51 |
| 1 | reverse | -1.78508 | -1.78296 | 0.00000 | -1.78508 | 0.00000 | 0.00212 | 0.00000 | 2026-03-19 10:02:56 |
| 1 | reverse | -3.54578 | -3.54541 | 0.00000 | -3.54578 | 0.00000 | 0.00037 | 0.00000 | 2026-03-19 10:03:00 |
| 1 | reverse | -5.00000 | -5.00024 | 0.00000 | -5.00000 | 0.00000 | -0.00024 | 0.00000 | 2026-03-19 10:03:05 |
| 2 | forward | -5.00000 | -5.00220 | 0.00000 | -5.00000 | 0.00000 | -0.00220 | 0.00000 | 2026-03-19 10:03:11 |
| 2 | forward | -3.54578 | -3.54785 | 0.00000 | -3.54578 | 0.00000 | -0.00207 | 0.00000 | 2026-03-19 10:03:16 |
| 2 | forward | -1.78508 | -1.78625 | 0.00017 | -1.78508 | 0.00000 | -0.00117 | 0.00000 | 2026-03-19 10:03:20 |
| 2 | forward | -0.66663 | -0.66724 | 0.00000 | -0.66664 | 0.00000 | -0.00060 | -0.00001 | 2026-03-19 10:03:25 |
| 2 | forward | 0.87005 | 0.86499 | 0.00000 | 0.87008 | 0.00000 | -0.00506 | 0.00003 | 2026-03-19 10:03:29 |
| 2 | forward | 2.40306 | 2.39941 | 0.00000 | 2.40305 | 0.00000 | -0.00365 | -0.00001 | 2026-03-19 10:03:34 |
| 2 | forward | 3.39152 | 3.39746 | 0.00000 | 3.39148 | 0.00000 | 0.00594 | -0.00004 | 2026-03-19 10:03:38 |
| 2 | forward | 5.00000 | 4.99683 | 0.00000 | 5.00000 | 0.00000 | -0.00317 | 0.00000 | 2026-03-19 10:03:42 |
| 2 | reverse | 5.00000 | 5.00098 | 0.00000 | 5.00000 | 0.00000 | 0.00098 | 0.00000 | 2026-03-19 10:03:49 |
| 2 | reverse | 3.39152 | 3.40039 | 0.00000 | 3.39156 | 0.00000 | 0.00887 | 0.00004 | 2026-03-19 10:03:54 |
| 2 | reverse | 2.40306 | 2.40210 | 0.00000 | 2.40305 | 0.00000 | -0.00096 | -0.00001 | 2026-03-19 10:03:58 |
| 2 | reverse | 0.87005 | 0.86841 | 0.00000 | 0.87000 | 0.00000 | -0.00164 | -0.00005 | 2026-03-19 10:04:02 |
| 2 | reverse | -0.66663 | -0.66431 | 0.00000 | -0.66664 | 0.00000 | 0.00233 | -0.00001 | 2026-03-19 10:04:07 |
| 2 | reverse | -1.78508 | -1.78369 | 0.00000 | -1.78508 | 0.00000 | 0.00139 | 0.00000 | 2026-03-19 10:04:11 |
| 2 | reverse | -3.54578 | -3.54517 | 0.00000 | -3.54578 | 0.00000 | 0.00062 | 0.00000 | 2026-03-19 10:04:16 |
| 2 | reverse | -5.00000 | -5.00024 | 0.00000 | -5.00000 | 0.00000 | -0.00024 | 0.00000 | 2026-03-19 10:04:20 |
| 3 | forward | -5.00000 | -5.00244 | 0.00000 | -5.00000 | 0.00000 | -0.00244 | 0.00000 | 2026-03-19 10:04:27 |
| 3 | forward | -3.54578 | -3.54810 | 0.00000 | -3.54578 | 0.00000 | -0.00231 | 0.00000 | 2026-03-19 10:04:31 |
| 3 | forward | -1.78508 | -1.78638 | 0.00000 | -1.78508 | 0.00000 | -0.00129 | 0.00000 | 2026-03-19 10:04:36 |
| 3 | forward | -0.66663 | -0.66724 | 0.00000 | -0.66664 | 0.00000 | -0.00060 | -0.00001 | 2026-03-19 10:04:40 |
| 3 | forward | 0.87005 | 0.86523 | 0.00000 | 0.87008 | 0.00000 | -0.00481 | 0.00003 | 2026-03-19 10:04:45 |
| 3 | forward | 2.40306 | 2.39917 | 0.00000 | 2.40305 | 0.00000 | -0.00389 | -0.00001 | 2026-03-19 10:04:49 |
| 3 | forward | 3.39152 | 3.39746 | 0.00000 | 3.39156 | 0.00000 | 0.00594 | 0.00004 | 2026-03-19 10:04:53 |
| 3 | forward | 5.00000 | 4.99780 | 0.00000 | 5.00000 | 0.00000 | -0.00220 | 0.00000 | 2026-03-19 10:04:58 |
| 3 | reverse | 5.00000 | 5.00098 | 0.00000 | 5.00000 | 0.00000 | 0.00098 | 0.00000 | 2026-03-19 10:05:05 |
| 3 | reverse | 3.39152 | 3.40063 | 0.00000 | 3.39156 | 0.00000 | 0.00911 | 0.00004 | 2026-03-19 10:05:09 |
| 3 | reverse | 2.40306 | 2.40259 | 0.00000 | 2.40305 | 0.00000 | -0.00047 | -0.00001 | 2026-03-19 10:05:13 |
| 3 | reverse | 0.87005 | 0.86841 | 0.00000 | 0.87000 | 0.00000 | -0.00164 | -0.00005 | 2026-03-19 10:05:18 |
| 3 | reverse | -0.66663 | -0.66479 | 0.00000 | -0.66664 | 0.00000 | 0.00184 | -0.00001 | 2026-03-19 10:05:22 |
| 3 | reverse | -1.78508 | -1.78369 | 0.00000 | -1.78508 | 0.00000 | 0.00139 | 0.00000 | 2026-03-19 10:05:27 |
| 3 | reverse | -3.54578 | -3.54517 | 0.00000 | -3.54578 | 0.00000 | 0.00062 | 0.00000 | 2026-03-19 10:05:31 |
| 3 | reverse | -5.00000 | -5.00024 | 0.00000 | -5.00000 | 0.00000 | -0.00024 | 0.00000 | 2026-03-19 10:05:36 |
| 4 | forward | -5.00000 | -5.00293 | 0.00000 | -5.00000 | 0.00000 | -0.00293 | 0.00000 | 2026-03-19 10:05:42 |
| 4 | forward | -3.54578 | -3.54810 | 0.00000 | -3.54578 | 0.00000 | -0.00231 | 0.00000 | 2026-03-19 10:05:47 |
| 4 | forward | -1.78508 | -1.78589 | 0.00000 | -1.78508 | 0.00000 | -0.00081 | 0.00000 | 2026-03-19 10:05:51 |
| 4 | forward | -0.66663 | -0.66675 | 0.00000 | -0.66664 | 0.00000 | -0.00012 | -0.00001 | 2026-03-19 10:05:56 |
| 4 | forward | 0.87005 | 0.86572 | 0.00000 | 0.87008 | 0.00000 | -0.00432 | 0.00003 | 2026-03-19 10:06:00 |
| 4 | forward | 2.40306 | 2.39893 | 0.00000 | 2.40305 | 0.00000 | -0.00413 | -0.00001 | 2026-03-19 10:06:05 |
| 4 | forward | 3.39152 | 3.39746 | 0.00000 | 3.39148 | 0.00000 | 0.00594 | -0.00004 | 2026-03-19 10:06:09 |
| 4 | forward | 5.00000 | 4.99707 | 0.00000 | 5.00000 | 0.00000 | -0.00293 | 0.00000 | 2026-03-19 10:06:13 |
| 4 | reverse | 5.00000 | 5.00000 | 0.00000 | 5.00000 | 0.00000 | 0.00000 | 0.00000 | 2026-03-19 10:06:20 |
| 4 | reverse | 3.39152 | 3.40088 | 0.00000 | 3.39156 | 0.00000 | 0.00936 | 0.00004 | 2026-03-19 10:06:25 |
| 4 | reverse | 2.40306 | 2.40210 | 0.00000 | 2.40305 | 0.00000 | -0.00096 | -0.00001 | 2026-03-19 10:06:29 |
| 4 | reverse | 0.87005 | 0.86792 | 0.00000 | 0.87000 | 0.00000 | -0.00213 | -0.00005 | 2026-03-19 10:06:33 |
| 4 | reverse | -0.66663 | -0.66431 | 0.00000 | -0.66664 | 0.00000 | 0.00233 | -0.00001 | 2026-03-19 10:06:38 |
| 4 | reverse | -1.78508 | -1.78296 | 0.00000 | -1.78508 | 0.00000 | 0.00212 | 0.00000 | 2026-03-19 10:06:42 |
| 4 | reverse | -3.54578 | -3.54565 | 0.00000 | -3.54578 | 0.00000 | 0.00013 | 0.00000 | 2026-03-19 10:06:47 |
| 4 | reverse | -5.00000 | -5.00073 | 0.00000 | -5.00000 | 0.00000 | -0.00073 | 0.00000 | 2026-03-19 10:06:51 |
| 5 | forward | -5.00000 | -5.00269 | 0.00000 | -5.00000 | 0.00000 | -0.00269 | 0.00000 | 2026-03-19 10:06:58 |
| 5 | forward | -3.54578 | -3.54834 | 0.00000 | -3.54578 | 0.00000 | -0.00256 | 0.00000 | 2026-03-19 10:07:02 |
| 5 | forward | -1.78508 | -1.78638 | 0.00000 | -1.78508 | 0.00000 | -0.00129 | 0.00000 | 2026-03-19 10:07:07 |
| 5 | forward | -0.66663 | -0.66699 | 0.00000 | -0.66664 | 0.00000 | -0.00036 | -0.00001 | 2026-03-19 10:07:11 |
| 5 | forward | 0.87005 | 0.86499 | 0.00000 | 0.87008 | 0.00000 | -0.00506 | 0.00003 | 2026-03-19 10:07:16 |
| 5 | forward | 2.40306 | 2.39893 | 0.00000 | 2.40305 | 0.00000 | -0.00413 | -0.00001 | 2026-03-19 10:07:20 |
| 5 | forward | 3.39152 | 3.39746 | 0.00000 | 3.39148 | 0.00000 | 0.00594 | -0.00004 | 2026-03-19 10:07:24 |
| 5 | forward | 5.00000 | 4.99683 | 0.00000 | 5.00000 | 0.00000 | -0.00317 | 0.00000 | 2026-03-19 10:07:29 |
| 5 | reverse | 5.00000 | 5.00024 | 0.00000 | 5.00000 | 0.00000 | 0.00024 | 0.00000 | 2026-03-19 10:07:36 |
| 5 | reverse | 3.39152 | 3.40039 | 0.00000 | 3.39156 | 0.00000 | 0.00887 | 0.00004 | 2026-03-19 10:07:40 |
| 5 | reverse | 2.40306 | 2.40161 | 0.00000 | 2.40305 | 0.00000 | -0.00145 | -0.00001 | 2026-03-19 10:07:44 |
| 5 | reverse | 0.87005 | 0.86841 | 0.00000 | 0.87008 | 0.00000 | -0.00164 | 0.00003 | 2026-03-19 10:07:49 |
| 5 | reverse | -0.66663 | -0.66431 | 0.00000 | -0.66664 | 0.00000 | 0.00233 | -0.00001 | 2026-03-19 10:07:53 |
| 5 | reverse | -1.78508 | -1.78296 | 0.00000 | -1.78508 | 0.00000 | 0.00212 | 0.00000 | 2026-03-19 10:07:58 |
| 5 | reverse | -3.54578 | -3.54517 | 0.00000 | -3.54578 | 0.00000 | 0.00062 | 0.00000 | 2026-03-19 10:08:02 |
| 5 | reverse | -5.00000 | -5.00098 | 0.00000 | -5.00000 | 0.00000 | -0.00098 | 0.00000 | 2026-03-19 10:08:07 |
