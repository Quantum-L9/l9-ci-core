from __future__ import annotations
import dataclasses, enum


class ResultStatus(str, enum.Enum):
    passed = "passed"
    failed = "failed"
    error  = "error"


@dataclasses.dataclass
class Finding:
    code:      str
    message:   str
    path:      str | None    = None
    line:      int | None    = None
    column:    int | None    = None
    reference: str | None    = None
    command:   str | None    = None
    details:   dict | None   = None

    def to_dict(self):
        d: dict = {"code": self.code, "message": self.message}
        for k in ("path","line","column","reference","command","details"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


@dataclasses.dataclass
class GateResult:
    gate_id:    str
    result:     ResultStatus
    violations: list = dataclasses.field(default_factory=list)
    warnings:   list = dataclasses.field(default_factory=list)
    metadata:   dict = dataclasses.field(default_factory=dict)

    def add_violation(self, code, message, **kw):
        self.violations.append(Finding(code=code, message=message, **kw))

    def add_warning(self, code, message, **kw):
        self.warnings.append(Finding(code=code, message=message, **kw))

    def to_dict(self):
        return {
            "schema_version": "1.0",
            "gate_id":    self.gate_id,
            "result":     self.result.value,
            "violations": [v.to_dict() for v in self.violations],
            "warnings":   [w.to_dict() for w in self.warnings],
            "metadata":   self.metadata,
        }

    def finalize(self):
        if self.result == ResultStatus.passed and self.violations:
            raise ValueError(f"{self.gate_id}: passed but violations present")
        if self.result in (ResultStatus.failed, ResultStatus.error) and not self.violations:
            raise ValueError(f"{self.gate_id}: {self.result.value} but no violations")
        return self
