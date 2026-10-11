"""One query the core repositories record alike, so Task 8 can search core across their fixtures."""
from datetime import date

from quarry.query import Query

CORE_MYELOMA = Query(phrases=("multiple myeloma",), since=date(2026, 9, 1), until=date(2026, 10, 10))
