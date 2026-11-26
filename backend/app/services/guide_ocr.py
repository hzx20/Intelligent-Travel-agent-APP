"""In-memory OCR through the configured Zhipu service; images are never persisted."""
import httpx
from io import BytesIO
import warnings
from PIL import Image, UnidentifiedImageError
from app.core.config import settings
from app.core.provider_budget import async_provider_slot, ProviderBusy


class OcrUnavailable(Exception):
    """A deliberately limited, user-facing failure reason."""


def validate_image(data: bytes, mime: str):
    """Reject fake signatures, truncated files and excessive decoded dimensions.

    Validation stays in memory; original bytes are sent only after explicit OCR
    submission. No storage or metadata-removal guarantee is introduced here.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != ('PNG' if mime == 'image/png' else 'JPEG'):
                    raise ValueError()
                if image.width * image.height > 20_000_000:
                    raise ValueError()
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError('图片无法解码或像素过大，请选择完整的 PNG/JPEG，最多 2000 万像素') from None


def parse_response(response):
    try:
        result = response.json()
    except ValueError:
        raise OcrUnavailable('图片识别暂不可用，请稍后重试') from None
    if response.status_code >= 400:
        message = result.get('message', '') if isinstance(result, dict) else ''
        if isinstance(message, str) and '账户已欠费' in message:
            raise OcrUnavailable('图片识别服务账户已欠费，请检查服务账户或手动粘贴正文')
        raise OcrUnavailable('图片识别暂不可用，请重试或手动粘贴正文')
    if not isinstance(result, dict) or result.get('status') != 'succeeded' or not isinstance(result.get('words_result'), list):
        raise OcrUnavailable('未能识别图片文字，请重试或手动粘贴正文')
    lines = [entry.get('words', '') for entry in result['words_result'] if isinstance(entry, dict) and isinstance(entry.get('words'), str)]
    text = '\n'.join(lines).strip()
    if len(text) > 12000:
        raise OcrUnavailable('识别文字过长，请使用较短的图片或手动粘贴正文')
    return text


async def recognize(data: bytes, mime: str) -> str:
    if not settings.zhipu_api_key:
        raise OcrUnavailable('尚未配置图片识别服务，请手动粘贴正文')
    try:
        async with async_provider_slot('ocr'):
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post('https://open.bigmodel.cn/api/paas/v4/files/ocr',
                    headers={'Authorization': f'Bearer {settings.zhipu_api_key}'},
                    data={'tool_type': 'hand_write', 'language_type': 'CHN_ENG'},
                    files={'file': ('guide.png' if mime == 'image/png' else 'guide.jpg', data, mime)})
    except ProviderBusy:
        raise OcrUnavailable('图片识别请求较多或额度暂不可用，请稍后重试') from None
    return parse_response(response)
