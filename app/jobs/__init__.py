"""백그라운드 처리. UI를 멈추지 않고, 앱을 껐다 켜도 이어서 처리한다."""

from .ai_tasks import SummaryRunner
from .ask_task import AskRunner
from .pipeline import Pipeline, PipelineRunner, StageReport

__all__ = ["AskRunner", "Pipeline", "PipelineRunner", "StageReport", "SummaryRunner"]
