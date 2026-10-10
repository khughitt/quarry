"""An adapter that needs no network, for framework tests."""
from quarry.errors import QuarryError
from quarry.results import Coverage, Result
from quarry.search import completeness


class Fake:
    match = "exact"
    filters = frozenset({"organism"})

    def __init__(self, resource):
        self.resource = resource

    def search(self, client, query):
        return self.prepare(query)(client)

    def prepare(self, query):
        native = query.native or " OR ".join(query.phrases)

        def execute(client):
            if "fail" in query.phrases:
                raise QuarryError("upstream_failed", f"{self.resource.id} is down")
            rows = [Result(id=f"geo:GSE{i}", resource=self.resource.id, kind="dataset", title=f"row {i}") for i in (1, 2, 3)]
            rows = rows[: query.limit] if query.limit else rows
            match = "native" if query.native is not None else self.match
            return (Coverage(self.resource.id, native, match, 3, len(rows),
                             completeness(total=3, returned=len(rows), limit=query.limit, short_page=False)), rows)

        return execute

    def show(self, client, curie):
        return Result(id=str(curie), resource=self.resource.id, kind="dataset", title="shown")


class Expanding(Fake):
    match = "expanded"
    filters = frozenset()


def make(resource):
    return Expanding(resource) if resource.id.startswith("expanding") else Fake(resource)
