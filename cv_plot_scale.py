"""Robust live autoscaling for potential-current plots."""

import numpy as np


def autoscale_cv_axes(axes, voltage, current):
    """Scale to the scan while preventing startup current spikes dominating Y."""
    x_values = np.asarray(voltage, dtype=float)
    y_values = np.asarray(current, dtype=float)
    finite = np.isfinite(x_values) & np.isfinite(y_values)
    x_values = x_values[finite]
    y_values = y_values[finite]
    if x_values.size == 0:
        return

    x_low, x_high = float(np.min(x_values)), float(np.max(x_values))
    if x_low == x_high:
        x_pad = max(abs(x_low) * 0.05, 0.01)
    else:
        x_pad = (x_high - x_low) * 0.05
    axes.set_xlim(x_low - x_pad, x_high + x_pad)

    # Once enough data exists, ignore the first few charging samples for the
    # Y limits and use robust percentiles.  The complete curve remains drawn
    # and the complete dataset is still written to CSV.
    scale_values = y_values
    if y_values.size >= 20:
        startup_count = min(50, max(5, int(y_values.size * 0.05)))
        scale_values = y_values[startup_count:]
    if scale_values.size >= 20:
        y_low, y_high = np.percentile(scale_values, (1.0, 99.0))
    else:
        y_low, y_high = np.min(scale_values), np.max(scale_values)
    y_low, y_high = float(y_low), float(y_high)
    if y_low == y_high:
        y_pad = max(abs(y_low) * 0.10, 1e-12)
    else:
        y_pad = (y_high - y_low) * 0.10
    axes.set_ylim(y_low - y_pad, y_high + y_pad)
