"""The one error type: a kind for the error object and a detail for a person."""


class QuarryError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
