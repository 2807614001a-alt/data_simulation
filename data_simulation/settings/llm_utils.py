import json
import os
from typing import Optional

from langchain_openai import ChatOpenAI


def _normalize_base_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    url = raw_url.strip().rstrip("/")
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _use_base_url() -> bool:
    raw = os.getenv("OPENAI_USE_BASE_URL", "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _resolve_model(default_model: str) -> str:
    env_model = os.getenv("OPENAI_MODEL")
    return env_model.strip() if env_model else default_model


def _fallback_models(default_model: str) -> list[str]:
    raw = os.getenv("OPENAI_FALLBACK_MODELS")
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
    else:
        # Conservative defaults for common third-party availability.
        models = [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ]
    # Remove duplicates and the default model.
    seen: set[str] = set()
    result: list[str] = []
    for m in models:
        if m == default_model or m in seen:
            continue
        seen.add(m)
        result.append(m)
    return result


def _is_model_not_found(err: Exception) -> bool:
    msg = str(err)
    return (
        "model_not_found" in msg
        or "无可用渠道" in msg
        or "distributor" in msg
        or "Error code: 503" in msg
    )


class LenientChatOpenAI(ChatOpenAI):
    def _parse_raw_response(self, raw_response, exc: Exception):
        if raw_response is None or not hasattr(raw_response, "http_response"):
            raise exc
        try:
            text = raw_response.http_response.text
        except Exception:
            raise exc
        try:
            return json.loads(text)
        except Exception:
            raise exc

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        original_model = getattr(self, "model_name", None)

        def _call_once():
            self._ensure_sync_client_available()
            payload = self._get_request_payload(messages, stop=stop, **kwargs)
            generation_info = None
            raw_response = None
            try:
                if "response_format" in payload:
                    payload.pop("stream", None)
                    raw_response = (
                        self.root_client.chat.completions.with_raw_response.parse(
                            **payload
                        )
                    )
                    try:
                        response = raw_response.parse()
                    except Exception as exc:
                        response = self._parse_raw_response(raw_response, exc)
                else:
                    raw_response = self.client.with_raw_response.create(**payload)
                    try:
                        response = raw_response.parse()
                    except Exception as exc:
                        response = self._parse_raw_response(raw_response, exc)
            except Exception as exc:
                if raw_response is not None and hasattr(raw_response, "http_response"):
                    exc.response = raw_response.http_response  # type: ignore[attr-defined]
                raise
            if self.include_response_headers and hasattr(raw_response, "headers"):
                generation_info = {"headers": dict(raw_response.headers)}
            return self._create_chat_result(response, generation_info)

        try:
            return _call_once()
        except Exception as exc:
            if not _is_model_not_found(exc):
                raise
            candidates = _fallback_models(original_model or "")
            last_exc: Exception = exc
            try:
                for model in candidates:
                    try:
                        self.model_name = model
                        return _call_once()
                    except Exception as retry_exc:
                        if _is_model_not_found(retry_exc):
                            last_exc = retry_exc
                            continue
                        raise
            finally:
                if original_model:
                    self.model_name = original_model
            raise last_exc

    def _create_chat_result(self, response, generation_info=None):
        if isinstance(response, str):
            text = response
            for _ in range(2):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Non-JSON response from API: {text[:200]}") from exc
                if isinstance(parsed, str):
                    text = parsed
                    continue
                response = parsed
                break
            if isinstance(response, str):
                raise ValueError(f"Non-JSON response from API: {response[:200]}")
        return super()._create_chat_result(response, generation_info)


def create_chat_llm(
    model: str,
    temperature: float | None = None,
    **kwargs,
):
    base_url = kwargs.pop("base_url", None)
    if base_url is None:
        if _use_base_url():
            base_url = _normalize_base_url(
                os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
            )
        else:
            # Force official endpoint unless explicitly opted into custom base_url.
            base_url = "https://api.openai.com/v1"
    kwargs.setdefault("use_responses_api", False)
    return LenientChatOpenAI(
        model=_resolve_model(model),
        temperature=temperature,
        base_url=base_url,
        **kwargs,
    )
