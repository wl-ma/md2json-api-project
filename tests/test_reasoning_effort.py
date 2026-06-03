from __future__ import annotations

from md2json_api.azure_extractor import AzureChatSectionExtractor
from md2json_api.cli import build_parser
from md2json_api.models import MarkdownSection, SectionContext


def test_cli_accepts_reasoning_effort_xhigh() -> None:
    args = build_parser().parse_args(["convert", "input.md", "--reasoning-effort", "xhigh"])

    assert args.reasoning_effort == "xhigh"


def test_azure_extractor_sends_reasoning_effort_and_completion_budget() -> None:
    fake_client = _FakeAzureClient()
    extractor = AzureChatSectionExtractor(
        model="gpt-5.5",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-10-21",
        max_output_tokens=123,
        reasoning_effort="xhigh",
    )
    extractor._client = fake_client

    items = extractor.extract_section(_section())

    request = fake_client.chat.completions.last_request
    assert items == []
    assert request["reasoning_effort"] == "xhigh"
    assert request["max_completion_tokens"] == 123
    assert "max_tokens" not in request


def _section() -> MarkdownSection:
    return MarkdownSection(
        index=1,
        context=SectionContext(
            chapter="Chapter 1",
            chapter_number="1",
            section="1.1 Test",
            section_number="1.1",
        ),
        text="Theorem 1. A statement.",
        start_line=1,
        end_line=1,
        heading_level=2,
        source_heading="1.1 Test",
    )


class _FakeAzureClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeCompletions:
    def __init__(self) -> None:
        self.last_request: dict | None = None

    def create(self, **request):
        self.last_request = request
        return _FakeResponse()


class _FakeMessage:
    content = '{"items": []}'


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]
    usage = None
