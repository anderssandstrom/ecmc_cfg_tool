"""Numerical ISO 230-style positioning statistics, independent of Qt and EPICS."""
import math

def _mean(values):
    return math.fsum(values)/len(values) if values else None

def _stddev(values):
    if len(values)<2:
        return None
    mean=_mean(values)
    return math.sqrt(math.fsum((v-mean)**2 for v in values)/(len(values)-1))

def linear_regression(points):
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        return {"slope": None, "intercept": None, "max_residual": None}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x_mean = _mean(xs)
    y_mean = _mean(ys)
    if x_mean is None or y_mean is None:
        return {"slope": None, "intercept": None, "max_residual": None}
    den = math.fsum((x - x_mean) * (x - x_mean) for x in xs)
    if den <= 0:
        return {"slope": None, "intercept": None, "max_residual": None}
    num = math.fsum((x - x_mean) * (y - y_mean) for x, y in pts)
    slope = num / den
    intercept = y_mean - slope * x_mean
    residuals = [abs(y - (intercept + slope * x)) for x, y in pts]
    return {"slope": slope, "intercept": intercept, "max_residual": max(residuals) if residuals else None}

def compute_metrics(rows, active=False):
    grouped = {}
    all_errors = []
    regression_points = []
    for row in rows:
        key = float(row['target'])
        bucket = grouped.setdefault(
            key,
            {
                "target": float(row["target"]),
                "forward": [],
                "reverse": [],
            },
        )
        if row["ref_error"] is not None and math.isfinite(float(row['ref_error'])):
            all_errors.append(float(row["ref_error"]))
        if row["reference_mean"] is not None and math.isfinite(float(row["reference_mean"])):
            regression_points.append((float(row["target"]), float(row["reference_mean"])))
        if row["direction"] == "forward" and row["ref_error"] is not None and math.isfinite(float(row["ref_error"])):
            bucket["forward"].append(float(row["ref_error"]))
        elif row["direction"] == "reverse" and row["ref_error"] is not None and math.isfinite(float(row["ref_error"])):
            bucket["reverse"].append(float(row["ref_error"]))

    per_target = []
    max_abs_error = None
    mean_bidirectional_values = []
    reversal_values = []
    directional_mean_values = []
    bidirectional_upper = []
    bidirectional_lower = []
    forward_axis_repeatability = None
    reverse_axis_repeatability = None
    overall_unidirectional_repeatability = None
    overall_bidirectional_repeatability = None
    for key in sorted(grouped.keys(), key=lambda k: grouped[k]["target"]):
        bucket = grouped[key]
        fwd = bucket["forward"]
        rev = bucket["reverse"]
        both = list(fwd) + list(rev)
        fwd_mean = _mean(fwd)
        rev_mean = _mean(rev)
        fwd_std = _stddev(fwd)
        rev_std = _stddev(rev)
        fwd_repeat = (4.0 * float(fwd_std)) if fwd_std is not None else None
        rev_repeat = (4.0 * float(rev_std)) if rev_std is not None else None
        if fwd_mean is not None:
            directional_mean_values.append(float(fwd_mean))
        if rev_mean is not None:
            directional_mean_values.append(float(rev_mean))
        if fwd_mean is not None and rev_mean is not None:
            mean_bidir = 0.5 * (float(fwd_mean) + float(rev_mean))
            reversal = float(fwd_mean) - float(rev_mean)
        else:
            mean_bidir = None
            reversal = None
        unidir_repeat = max(v for v in (fwd_repeat, rev_repeat) if v is not None) if (fwd_repeat is not None or rev_repeat is not None) else None
        upper_candidates = []
        lower_candidates = []
        if fwd_mean is not None and fwd_std is not None:
            f_half = 0.5 * float(fwd_repeat or 0.0)
            upper_candidates.append(float(fwd_mean) + f_half)
            lower_candidates.append(float(fwd_mean) - f_half)
        if rev_mean is not None and rev_std is not None:
            r_half = 0.5 * float(rev_repeat or 0.0)
            upper_candidates.append(float(rev_mean) + r_half)
            lower_candidates.append(float(rev_mean) - r_half)
        bidir_upper = max(upper_candidates) if upper_candidates else None
        bidir_lower = min(lower_candidates) if lower_candidates else None
        if fwd_std is not None and rev_std is not None and reversal is not None:
            bidir_repeat = max(
                (2.0 * float(fwd_std)) + (2.0 * float(rev_std)) + abs(float(reversal)),
                float(fwd_repeat or 0.0),
                float(rev_repeat or 0.0),
            )
        else:
            bidir_repeat = None
        point_max_abs = max((abs(v) for v in both), default=None)
        if point_max_abs is not None:
            max_abs_error = point_max_abs if max_abs_error is None else max(max_abs_error, point_max_abs)
        if mean_bidir is not None:
            mean_bidirectional_values.append(float(mean_bidir))
        if reversal is not None:
            reversal_values.append(float(reversal))
        if bidir_upper is not None:
            bidirectional_upper.append(float(bidir_upper))
        if bidir_lower is not None:
            bidirectional_lower.append(float(bidir_lower))
        if fwd_repeat is not None:
            forward_axis_repeatability = (
                float(fwd_repeat)
                if forward_axis_repeatability is None
                else max(forward_axis_repeatability, float(fwd_repeat))
            )
        if rev_repeat is not None:
            reverse_axis_repeatability = (
                float(rev_repeat)
                if reverse_axis_repeatability is None
                else max(reverse_axis_repeatability, float(rev_repeat))
            )
        if unidir_repeat is not None:
            overall_unidirectional_repeatability = (
                unidir_repeat
                if overall_unidirectional_repeatability is None
                else max(overall_unidirectional_repeatability, unidir_repeat)
            )
        if bidir_repeat is not None:
            overall_bidirectional_repeatability = (
                bidir_repeat
                if overall_bidirectional_repeatability is None
                else max(overall_bidirectional_repeatability, bidir_repeat)
            )
        per_target.append(
            {
                "target": bucket["target"],
                "forward_mean": fwd_mean,
                "reverse_mean": rev_mean,
                "mean_bidirectional_deviation": mean_bidir,
                "forward_std": fwd_std,
                "reverse_std": rev_std,
                "forward_min": min(fwd) if fwd else None,
                "forward_max": max(fwd) if fwd else None,
                "reverse_min": min(rev) if rev else None,
                "reverse_max": max(rev) if rev else None,
                "forward_count": len(fwd),
                "reverse_count": len(rev),
                "reversal_value": reversal,
                "reversal_magnitude": abs(float(reversal)) if reversal is not None else None,
                "forward_repeatability": fwd_repeat,
                "reverse_repeatability": rev_repeat,
                "unidirectional_repeatability": unidir_repeat,
                "bidirectional_repeatability": bidir_repeat,
                "forward_lower_limit": (float(fwd_mean) - (0.5 * float(fwd_repeat))) if (fwd_mean is not None and fwd_repeat is not None) else None,
                "forward_upper_limit": (float(fwd_mean) + (0.5 * float(fwd_repeat))) if (fwd_mean is not None and fwd_repeat is not None) else None,
                "reverse_lower_limit": (float(rev_mean) - (0.5 * float(rev_repeat))) if (rev_mean is not None and rev_repeat is not None) else None,
                "reverse_upper_limit": (float(rev_mean) + (0.5 * float(rev_repeat))) if (rev_mean is not None and rev_repeat is not None) else None,
                "bidirectional_upper_limit": bidir_upper,
                "bidirectional_lower_limit": bidir_lower,
                "max_abs_error": point_max_abs,
            }
        )

    mean_bias = _mean(all_errors)
    regression = linear_regression(regression_points)
    range_mean_bidirectional = (
        max(mean_bidirectional_values) - min(mean_bidirectional_values)
        if len(mean_bidirectional_values) >= 2
        else None
    )
    mean_reversal_value = _mean(reversal_values)
    maximum_reversal_value = max((abs(v) for v in reversal_values), default=None)
    bidirectional_systematic_deviation = (
        max(directional_mean_values) - min(directional_mean_values)
        if len(directional_mean_values) >= 2
        else None
    )
    bidirectional_accuracy = (
        max(bidirectional_upper) - min(bidirectional_lower)
        if bidirectional_upper and bidirectional_lower
        else None
    )
    complete = bool(per_target) and all(p['forward_count']>=2 and p['reverse_count']>=2 for p in per_target)
    return {
        'bidirectional_complete': complete,
        "state": "Complete" if (not active and rows) else ("Running" if active else "Idle"),
        "target_count": len(grouped),
        "samples_total": len(rows),
        "max_abs_error": max_abs_error,
        "range_mean_bidirectional_deviation": range_mean_bidirectional if complete else None,
        "mean_reversal_value": mean_reversal_value if complete else None,
        "maximum_reversal_value": maximum_reversal_value if complete else None,
        "forward_axis_repeatability": forward_axis_repeatability,
        "reverse_axis_repeatability": reverse_axis_repeatability,
        "unidirectional_repeatability": overall_unidirectional_repeatability,
        "bidirectional_repeatability": overall_bidirectional_repeatability if complete else None,
        "bidirectional_systematic_deviation": bidirectional_systematic_deviation if complete else None,
        "bidirectional_accuracy": bidirectional_accuracy if complete else None,
        "mean_bias": mean_bias,
        "linearity": regression.get("max_residual"),
        "fit_slope": regression.get("slope"),
        "fit_intercept": regression.get("intercept"),
        "per_target": per_target,
    }

