"""IQ sample parser interface.

Contract: accepts (descriptor, bytes), returns normalized float32 samples.
Stateless. Source-agnostic. Matches iq_input_schema.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np
import numpy.typing as npt

from agent.domain import IQDescriptor


class IQParseErrorCode(enum.Enum):
    EMPTY_BUFFER = "EMPTY_BUFFER"
    INCOMPLETE_SAMPLE = "INCOMPLETE_SAMPLE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_LAYOUT = "UNSUPPORTED_LAYOUT"
    INVALID_DESCRIPTOR = "INVALID_DESCRIPTOR"


@dataclass(frozen=True)
class IQParseError:
    code: IQParseErrorCode
    message: str
    offset: Optional[int] = None


@dataclass(frozen=True)
class IQParseResult:
    """Parsed IQ samples.

    `samples` is a float32 ndarray of interleaved I/Q values.
    Length = sample_count * 2.  Normalized to [-1.0, 1.0].
    DC offset removed if descriptor.dc_offset_remove is True.
    """

    samples: npt.NDArray[np.float32]
    sample_count: int


def parse_iq(
    descriptor: IQDescriptor, buffer: bytes
) -> Union[IQParseResult, IQParseError]:
    """Parse raw IQ bytes into normalized float32 samples.

    This is the interface. Implementation goes in source/iq_parser.py.

    Invariants (from iq_input_schema.md):
    - len(samples) == sample_count * 2
    - sample_count == len(buffer) / descriptor.bytes_per_sample
    - all samples in [-1.0, 1.0] when normalize=True
    - mean(I) ≈ 0 and mean(Q) ≈ 0 when dc_offset_remove=True
    """
    raise NotImplementedError
