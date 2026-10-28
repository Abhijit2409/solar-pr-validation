"""Unit standardisation and observation-state vocabulary.

Every conversion is linear: standardized = raw * multiplier + offset, recorded
per row as `conversion_rule`, even when the multiplier is 1.
"""
import numpy as np
import pandas as pd

# Controlled vocabulary of observation states (docs/data_dictionary.md).
OBSERVED_VALID = "OBSERVED_VALID"
OBSERVED_ZERO = "OBSERVED_ZERO"
SOURCE_NULL = "SOURCE_NULL"
EXPECTED_ROW_MISSING = "EXPECTED_ROW_MISSING"
DUPLICATE_IDENTICAL = "DUPLICATE_IDENTICAL"
DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
INVALID_PHYSICAL = "INVALID_PHYSICAL"
TIME_AMBIGUOUS = "TIME_AMBIGUOUS"
EXCLUDED_BY_SCOPE = "EXCLUDED_BY_SCOPE"

STATES = [OBSERVED_VALID, OBSERVED_ZERO, SOURCE_NULL, EXPECTED_ROW_MISSING, DUPLICATE_IDENTICAL,
          DUPLICATE_CONFLICT, INVALID_PHYSICAL, TIME_AMBIGUOUS, EXCLUDED_BY_SCOPE]

# States whose row represents its interval in the resolved v1 stream.
RESOLVED_STATES = {OBSERVED_VALID, OBSERVED_ZERO, SOURCE_NULL, INVALID_PHYSICAL}


def parse_raw_value(raw_text):
    """Numeric value from raw text; blank or non-numeric text becomes NaN."""
    return pd.to_numeric(pd.Series(raw_text).replace("", np.nan), errors="coerce")


def standardize(raw_numeric, multiplier, offset, raw_unit, standardized_unit):
    """Apply a linear unit rule. Returns (standardized values, rule text)."""
    rule = f"{standardized_unit} = {raw_unit} * {multiplier:g} + {offset:g}"
    return raw_numeric * multiplier + offset, rule


def value_state(standardized, vmin, vmax):
    """Value-only state: SOURCE_NULL, INVALID_PHYSICAL, OBSERVED_ZERO or OBSERVED_VALID."""
    v = pd.Series(standardized).reset_index(drop=True)
    state = pd.Series(OBSERVED_VALID, index=v.index, dtype="object")
    state[v == 0] = OBSERVED_ZERO
    state[(v < vmin) | (v > vmax)] = INVALID_PHYSICAL
    state[v.isna()] = SOURCE_NULL
    return state


def values_equal(a, b, tol):
    """Element-wise equality within an absolute tolerance; two NaNs count as equal."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return (np.abs(a - b) <= tol) | (np.isnan(a) & np.isnan(b))
