"""Offline analysis of ISO230 datasets and observed reference settling."""
import math
from ecmc_iso230_math import compute_metrics

METRICS=(
 ('bidirectional_accuracy','Bidirectional accuracy A'),
 ('bidirectional_repeatability','Bidirectional repeatability R'),
 ('bidirectional_systematic_deviation','Systematic deviation E'),
 ('range_mean_bidirectional_deviation','Mean deviation range M'),
 ('mean_reversal_value','Mean reversal B'),
 ('maximum_reversal_value','Maximum reversal'),
 ('forward_axis_repeatability','Forward repeatability'),
 ('reverse_axis_repeatability','Reverse repeatability'),
 ('mean_bias','Mean bias'),('fit_slope','Fit slope'),('fit_intercept','Fit intercept'),
 ('linearity','Maximum fit residual'))

def session_metrics(payload):
    if payload.get('file_type') not in (None,'','ecmc_iso230_session'):
        raise ValueError('Not an ISO230 session')
    rows=[]
    for source in payload.get('measurements',[]):
        row=dict(source)
        row['reference_mean']=source.get('adjusted_reference_mean',source.get('reference_mean'))
        row['ref_error']=source.get('adjusted_ref_error',source.get('ref_error'))
        rows.append(row)
    if not rows:
        raise ValueError('Session has no measurements')
    return compute_metrics(rows)

def compare_metrics(old,new):
    return [(label,old.get(key),new.get(key),
             new[key]-old[key] if old.get(key) is not None and new.get(key) is not None else None)
            for key,label in METRICS]

def compensation_rows(metrics,expected_targets=None,cycles=None):
    rows=metrics.get('per_target') or []
    if not rows or not metrics.get('bidirectional_complete'):
        raise ValueError('Complete bidirectional measurements are required')
    if expected_targets is not None and {float(r['target']) for r in rows}!={float(t) for t in expected_targets}:
        raise ValueError('Not all planned targets were measured')
    if cycles is not None and any(r['forward_count']!=int(cycles) or r['reverse_count']!=int(cycles) for r in rows):
        raise ValueError('Not all planned cycles were measured')
    return [dict(position=r['target'],mean_error=r['mean_bidirectional_deviation'],
                 position_correction=-r['mean_bidirectional_deviation'],
                 forward_correction=-r['forward_mean'],reverse_correction=-r['reverse_mean'],
                 reversal_forward_minus_reverse=r['reversal_value'])
            for r in rows]

def settling_time(trace,final,tolerance,min_hold):
    if not math.isfinite(final) or tolerance<=0 or min_hold<=0:
        raise ValueError('Invalid settling parameters')
    points=sorted((float(t),float(v)) for t,v in trace)
    if len(points)<2 or any(not math.isfinite(t) or not math.isfinite(v) for t,v in points):
        return None
    start=None
    for t,v in points:
        if abs(v-final)>tolerance:
            start=None
        elif start is None:
            start=t
    if start is None or points[-1][0]-start<min_hold:
        return None
    return start
