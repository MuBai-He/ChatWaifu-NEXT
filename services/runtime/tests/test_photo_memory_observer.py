"""Photo classifier and observation lifecycle regressions."""
# pyright: reportPrivateUsage=false

import asyncio
import io
import math
from collections.abc import AsyncGenerator, Callable
from typing import Any, Literal, cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.photo_memory import PhotoMemorySettings
from chatwaifu_runtime.photo_memory.classifier import PhotoClassification, PhotoClassifier
from chatwaifu_runtime.photo_memory.observer import PhotoMemoryObserver, PhotoObservationSource
from chatwaifu_runtime.photo_memory.ports import PhotoMemoryRepository
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallRequested,
)
from PIL import Image


def create_test_image(
    size: tuple[int, int] = (100, 100),
    format: str = "JPEG",
    exif: bool = False,
    empty: bool = False,
) -> LlmInputImage:
    if empty:
        return LlmInputImage(data=b"", mime_type="image/jpeg")
    img = Image.new("RGB", size, color="red")
    buf = io.BytesIO()
    if exif:
        exif_dict = img.getexif()
        exif_dict[0x0112] = 6
        img.save(buf, format=format, exif=exif_dict)
    else:
        img.save(buf, format=format)

    mime: Literal["image/jpeg", "image/png"] = "image/jpeg"
    if format.upper() == "PNG":
        mime = "image/png"
    return LlmInputImage(data=buf.getvalue(), mime_type=mime)


class MockLlmProvider:
    def __init__(self) -> None:
        self.stream_func: Callable[..., AsyncGenerator[LlmStreamEvent, None]] | None = None

    @property
    def kind(self) -> str:
        return "mock"

    @property
    def supports_tool_calling(self) -> bool:
        return True

    async def stream(self, request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        if self.stream_func:
            async for event in self.stream_func(request):
                yield event


@pytest.fixture
def mock_llm() -> MockLlmProvider:
    return MockLlmProvider()


@pytest.fixture
def classifier(mock_llm: MockLlmProvider) -> PhotoClassifier:
    return PhotoClassifier(llm=mock_llm)


class MockPhotoMemoryRepository:
    def __init__(self) -> None:
        self.settings = PhotoMemorySettings(retention_enabled=True, revision=1)
        self.save_called = 0
        self.save_kwargs: dict[str, Any] = {}
        self.get_settings_event: asyncio.Event | None = None

    async def get_settings(self, scope: str, character_id: str) -> PhotoMemorySettings:
        if self.get_settings_event:
            await self.get_settings_event.wait()
        return self.settings

    async def save(
        self, scope: str, character_id: str, candidate: Any, *, expected_revision: int
    ) -> bool:
        self.save_called += 1
        self.save_kwargs = {"candidate": candidate, "expected_revision": expected_revision}
        return True


@pytest.fixture
def mock_repository() -> MockPhotoMemoryRepository:
    return MockPhotoMemoryRepository()


@pytest.fixture
def observer(
    mock_repository: MockPhotoMemoryRepository, classifier: PhotoClassifier
) -> PhotoMemoryObserver:
    obs = PhotoMemoryObserver(
        repository=cast(PhotoMemoryRepository, mock_repository), classifier=classifier
    )
    obs.start()
    return obs


@pytest.mark.asyncio
async def test_classification_validation() -> None:
    c = PhotoClassification.model_validate(
        {
            "suitable": True,
            "confidence": 0.95,
            "title": "A cat",
            "description": "A cute cat on the sofa.",
            "keywords": ["cat", "sofa"],
        }
    )
    assert c.suitable is True

    with pytest.raises(ValueError):
        PhotoClassification.model_validate(
            {
                "suitable": True,
                "confidence": 0.95,
                "title": "A cat",
                "description": "A cute cat on the sofa.",
                "keywords": [],
            }
        )

    with pytest.raises(ValueError):
        PhotoClassification.model_validate(
            {
                "suitable": True,
                "confidence": 0.95,
                "title": "A cat",
                "description": "A cute cat on the sofa.",
                "keywords": ["k1"] * 13,
            }
        )

    with pytest.raises(ValueError):
        PhotoClassification.model_validate(
            {
                "suitable": True,
                "confidence": math.inf,
                "title": "A cat",
                "description": "A cute cat",
                "keywords": ["cat"],
            }
        )


@pytest.mark.asyncio
async def test_classifier_success(classifier: PhotoClassifier, mock_llm: MockLlmProvider) -> None:
    async def mock_stream(request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        yield LlmToolCallRequested(
            call=LlmToolCall(
                call_id="call_1",
                name="classify_photo",
                arguments={
                    "suitable": True,
                    "confidence": 0.95,
                    "title": "Test Photo",
                    "description": "Test description of photo",
                    "keywords": ["test", "photo"],
                },
            )
        )

    mock_llm.stream_func = mock_stream
    img = create_test_image()
    res = await classifier.classify(img, generation_id=uuid4())
    assert res is not None
    assert res.suitable is True


@pytest.mark.asyncio
async def test_classifier_low_confidence(
    classifier: PhotoClassifier, mock_llm: MockLlmProvider
) -> None:
    async def mock_stream(request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        yield LlmToolCallRequested(
            call=LlmToolCall(
                call_id="call_1",
                name="classify_photo",
                arguments={
                    "suitable": True,
                    "confidence": 0.85,
                    "title": "Test",
                    "description": "Desc",
                    "keywords": ["test"],
                },
            )
        )

    mock_llm.stream_func = mock_stream
    res = await classifier.classify(create_test_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_classifier_refusal(classifier: PhotoClassifier, mock_llm: MockLlmProvider) -> None:
    async def mock_stream(request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        yield LlmTextDelta(text="I cannot do that.")

    mock_llm.stream_func = mock_stream
    res = await classifier.classify(create_test_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_classifier_timeout(
    monkeypatch: pytest.MonkeyPatch, classifier: PhotoClassifier, mock_llm: MockLlmProvider
) -> None:
    import chatwaifu_runtime.photo_memory.classifier

    monkeypatch.setattr(
        chatwaifu_runtime.photo_memory.classifier, "CLASSIFICATION_TIMEOUT_SECONDS", 0.05
    )

    async def mock_stream(request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        await asyncio.Event().wait()
        yield LlmToolCallRequested(
            call=LlmToolCall(call_id="c1", name="classify_photo", arguments={})
        )

    mock_llm.stream_func = mock_stream
    res = await classifier.classify(create_test_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_observer_disabled(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    mock_repository.settings = PhotoMemorySettings(retention_enabled=False, revision=1)
    source = PhotoObservationSource("test", "char1", uuid4(), uuid4())

    wait_called = False

    async def wait() -> bool:
        nonlocal wait_called
        wait_called = True
        return True

    await observer.observe(source, create_test_image(), wait_for_completion=wait)
    await asyncio.gather(*[t for _, t in observer._tasks.values()])

    assert not wait_called
    assert mock_repository.save_called == 0


@pytest.mark.asyncio
async def test_observer_success_and_exif(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    async def mock_stream(request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        yield LlmToolCallRequested(
            call=LlmToolCall(
                call_id="c1",
                name="classify_photo",
                arguments={
                    "suitable": True,
                    "confidence": 0.95,
                    "title": "t",
                    "description": "d",
                    "keywords": ["k"],
                },
            )
        )

    mock_llm.stream_func = mock_stream

    source = PhotoObservationSource("test", "char1", uuid4(), uuid4())

    async def wait() -> bool:
        return True

    img = create_test_image(size=(300, 200), exif=True)
    await observer.observe(source, img, wait_for_completion=wait)

    await asyncio.gather(*[t for _, t in observer._tasks.values()])

    assert mock_repository.save_called == 1
    candidate = mock_repository.save_kwargs["candidate"]
    assert candidate.title == "t"

    assert (candidate.width, candidate.height) == (200, 300)
    assert len(candidate.data) > 0
    with Image.open(io.BytesIO(candidate.data)) as saved_img:
        exif = saved_img.getexif()
        assert not exif or not exif.get(0x0112)


@pytest.mark.asyncio
async def test_observer_capacity(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    stream_event = asyncio.Event()

    async def mock_stream(request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        await stream_event.wait()
        yield LlmToolCallRequested(
            call=LlmToolCall(
                call_id="c1",
                name="classify_photo",
                arguments={
                    "suitable": True,
                    "confidence": 0.95,
                    "title": "t",
                    "description": "d",
                    "keywords": ["k"],
                },
            )
        )

    mock_llm.stream_func = mock_stream

    s1 = PhotoObservationSource("test", "char1", uuid4(), uuid4())
    s2 = PhotoObservationSource("test", "char1", uuid4(), uuid4())
    s3 = PhotoObservationSource("test", "char1", uuid4(), uuid4())

    async def wait() -> bool:
        return True

    await observer.observe(s1, create_test_image(), wait_for_completion=wait)
    await observer.observe(s2, create_test_image(), wait_for_completion=wait)
    await observer.observe(s3, create_test_image(), wait_for_completion=wait)

    assert len(observer._tasks) == 2

    stream_event.set()
    await asyncio.gather(*[t for _, t in observer._tasks.values()])
    assert mock_repository.save_called == 2


@pytest.mark.asyncio
async def test_observer_cancel_generation_during_settings(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    mock_repository.get_settings_event = asyncio.Event()

    s = PhotoObservationSource("test", "char1", uuid4(), uuid4())

    async def wait() -> bool:
        return True

    await observer.observe(s, create_test_image(), wait_for_completion=wait)

    assert len(observer._tasks) == 1

    await observer.cancel_generation(s.generation_id)
    mock_repository.get_settings_event.set()

    assert not observer._tasks
    assert mock_repository.save_called == 0


@pytest.mark.asyncio
async def test_observer_cancel_connection(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    mock_repository.get_settings_event = asyncio.Event()

    conn_id = uuid4()
    s = PhotoObservationSource("test", "char1", conn_id, uuid4())

    async def wait() -> bool:
        return True

    await observer.observe(s, create_test_image(), wait_for_completion=wait)
    await observer.cancel_connection(conn_id)

    mock_repository.get_settings_event.set()

    assert not observer._tasks
    assert mock_repository.save_called == 0


@pytest.mark.asyncio
async def test_observer_stop_and_restart(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    mock_repository.get_settings_event = asyncio.Event()

    s = PhotoObservationSource("test", "char1", uuid4(), uuid4())

    async def wait() -> bool:
        return True

    await observer.observe(s, create_test_image(), wait_for_completion=wait)

    await observer.stop()

    assert not observer._tasks
    assert observer._stop_fence is None

    observer.start()
    mock_repository.get_settings_event.set()

    assert mock_repository.save_called == 0


@pytest.mark.asyncio
async def test_normalize_photo_empty_and_size() -> None:
    from chatwaifu_runtime.photo_memory.observer import _normalize_photo

    with pytest.raises(ValueError, match="empty image"):
        empty_img = type("MockImage", (), {"data": b"", "mime_type": "image/jpeg"})()
        _normalize_photo(cast(LlmInputImage, empty_img))

    large_image = type(
        "MockImage", (), {"data": b"a" * (5 * 1024 * 1024 + 1), "mime_type": "image/jpeg"}
    )()
    with pytest.raises(ValueError, match="input photo exceeds size limit"):
        _normalize_photo(cast(LlmInputImage, large_image))


@pytest.mark.asyncio
async def test_duplicate_enqueue_regression(
    observer: PhotoMemoryObserver,
    mock_repository: MockPhotoMemoryRepository,
    mock_llm: MockLlmProvider,
) -> None:
    mock_repository.get_settings_event = asyncio.Event()
    s = PhotoObservationSource("test", "char1", uuid4(), uuid4())

    async def wait() -> bool:
        return True

    await observer.observe(s, create_test_image(), wait_for_completion=wait)
    await observer.observe(s, create_test_image(), wait_for_completion=wait)
    assert len(observer._tasks) == 1
    mock_repository.get_settings_event.set()
