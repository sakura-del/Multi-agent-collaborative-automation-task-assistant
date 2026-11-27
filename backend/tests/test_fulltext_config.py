from app.core.config import settings
from app.orchestration.contracts import LiteratureReviewRequest
from app.schemas.task import TaskCreate


def test_fulltext_config_defaults():
    cfg = settings.fulltext
    assert cfg.enabled is True
    assert cfg.max_documents_per_task == 8
    assert cfg.per_document_timeout_seconds == 20.0
    assert cfg.total_timeout_seconds == 60.0
    assert cfg.max_concurrency == 4
    assert "cc-by" in cfg.license_whitelist


def test_task_create_full_text_default_true_and_can_disable():
    assert TaskCreate(topic="测试主题五个字", keywords=["k"]).full_text is True
    assert TaskCreate(topic="测试主题五个字", keywords=["k"], full_text=False).full_text is False


def test_literature_review_request_full_text_default_true():
    req = LiteratureReviewRequest(topic="测试主题五个字", keywords=["k"])
    assert req.full_text is True
