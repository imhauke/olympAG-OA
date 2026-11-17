"""Mutable document state kept outside checkpointed message history."""

from collections.abc import Callable
from dataclasses import dataclass

from docx_agent.document.blocks import BlockIndex
from docx_agent.document.integrity import IntegrityReport, check
from docx_agent.document.ops import EditResult
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import paragraph_text
from docx_agent.document.styles import StyleCatalog


@dataclass(frozen=True)
class _Receipt:
    result: EditResult
    before: bytes
    after: bytes
    before_id: str | None
    after_id: str | None
    touched: frozenset[str]


class DocumentSession:
    """Own one document and a verifiable journal of successful operations.

    Per-operation snapshots let validation check intermediate effects even when later
    operations edit the same block, renumber paragraphs on disk, or cancel an earlier edit.
    Only the journal of the current attempt is retained after a reset.
    """

    def __init__(self, original: bytes) -> None:
        self.original = original
        self.reset()

    def reset(self) -> None:
        self.package = DocxPackage.from_bytes(self.original)
        self.index = BlockIndex.from_package(self.package)
        self.styles = StyleCatalog.from_package(self.package)
        self._receipts: list[_Receipt] = []
        self._baseline = self.package.to_bytes()

    @property
    def edits(self) -> list[EditResult]:
        return [r.result for r in self._receipts]

    def _disk_ids(self) -> dict[str, str]:
        # Re-index the same XML without mutating it: serialized documents don't contain
        # our stable IDs, so integrity reports use IDs assigned in current document order.
        disk = BlockIndex(self.index.body)
        return {bid: str(disk.id_of(self.index.get(bid))) for bid in self.index.ids()}

    def apply(self, operation: Callable[[], EditResult]) -> EditResult:
        before = self.package.to_bytes()
        ids = self._disk_ids()
        result = operation()
        after_ids = self._disk_ids()
        touched = {ids[b] for b in result.touched if b in ids}
        touched.update(b for b in result.touched if b not in ids)
        self._receipts.append(
            _Receipt(
                result,
                before,
                self.package.to_bytes(),
                ids.get(result.block_id),
                after_ids.get(result.block_id),
                frozenset(touched),
            )
        )
        return result

    def validate(self) -> IntegrityReport:
        """Check final integrity, journal continuity, and each claimed operation effect."""
        touched = {bid for e in self.edits for bid in e.touched}
        removed = {category for e in self.edits for category in e.removed}
        styles = {
            e.block_id: e.style
            for e in self.edits
            if e.style is not None and e.block_id in self.index and not e.block_id.startswith("n")
        }
        output = self.package.to_bytes()
        report = check(
            self.original, output, touched=touched, allow_removed=removed, style_changes=styles
        )
        previous = self._baseline
        for i, receipt in enumerate(self._receipts, 1):
            if receipt.before != previous:
                report.violations.append(f"Unjournaled mutation before operation {i}.")
            proof = check(
                receipt.before,
                receipt.after,
                touched=receipt.touched,
                allow_removed=receipt.result.removed,
                style_changes=(
                    {receipt.before_id: receipt.result.style}
                    if receipt.before_id is not None and receipt.result.style is not None
                    else None
                ),
            )
            report.violations.extend(f"Operation {i}: {v}" for v in proof.violations)
            if not _effect_matches(receipt, proof):
                report.violations.append(
                    f"Operation {i} effect does not match {receipt.result.op} "
                    f"on {receipt.result.block_id}."
                )
            previous = receipt.after
        if output != previous:
            report.violations.append("Unjournaled mutation after the last operation.")
        return report


def _effect_matches(receipt: _Receipt, report: IntegrityReport) -> bool:
    result = receipt.result
    kind = {"insert_paragraph": "inserted", "delete_paragraph": "deleted"}.get(
        result.op, "modified"
    )
    for change in report.changes:
        if (
            change.kind == kind
            and change.before_id == receipt.before_id
            and change.after_id == receipt.after_id
            and change.before_text == result.before
            and change.after_text == result.after
        ):
            return True
    # An exact no-op replacement is legal, as is a sequence whose *net* effect is zero.
    if kind == "modified" and result.before == result.after and receipt.before == receipt.after:
        index = BlockIndex.from_package(DocxPackage.from_bytes(receipt.after))
        return receipt.after_id is not None and (
            paragraph_text(index.paragraph(receipt.after_id)) == result.after
        )
    return False
