from dataclasses import asdict, dataclass


@dataclass(slots=True)
class DocumentChunk:
    document_id: str
    document_name: str
    source: str
    version: str
    effective_date: str | None
    chunk_id: str
    page: int | None
    is_approved: bool
    text: str

    def to_dict(self) -> dict:
        return asdict(self)

