import logging
import time

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def _token_count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class GeminiClient:
    """Lazy Gemini API adapter with a bounded request timeout."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_ms: int = 30_000,
        fallback_models: tuple[str, ...] | list[str] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_ms = timeout_ms
        self.fallback_models = tuple(fallback_models or ())
        self.client = None

    def _get_client(self):
        # Retrieval from the local knowledge hub must still work when Gemini is
        # not configured. Validate/create the provider client only on escalation.
        if not self.api_key:
            logger.error("gemini.client_unavailable reason=missing_api_key")
            raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file.")
        if self.client is None:
            logger.info("gemini.client_initializing model=%s timeout_ms=%d", self.model, self.timeout_ms)
            self.client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=self.timeout_ms),
            )
        return self.client

    def generate(self, contents: str) -> tuple[str, dict[str, int]]:
        started = time.perf_counter()
        model_names = []
        for model_name in (self.model, *self.fallback_models):
            if model_name and model_name not in model_names:
                model_names.append(model_name)

        last_error = None
        for attempt, model_name in enumerate(model_names, 1):
            logger.info("gemini.request_started model=%s prompt_chars=%d timeout_ms=%d attempt=%d", model_name, len(contents or ""), self.timeout_ms, attempt)
            try:
                response = self._get_client().models.generate_content(
                    model=model_name,
                    contents=contents,
                )
                if response is None:
                    raise RuntimeError("Gemini returned no response.")

                text = getattr(response, "text", None)
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError("Gemini returned an empty response.")

                usage = getattr(response, "usage_metadata", None)
                usage_data = {
                    "input_tokens": _token_count(getattr(usage, "prompt_token_count", 0)),
                    "output_tokens": _token_count(getattr(usage, "candidates_token_count", 0)),
                }
                if model_name != self.model:
                    logger.warning("gemini.fallback_model_used requested_model=%s used_model=%s", self.model, model_name)
                    self.model = model_name
                logger.info("gemini.request_succeeded model=%s input_tokens=%d output_tokens=%d response_chars=%d elapsed_ms=%.1f", model_name, usage_data["input_tokens"], usage_data["output_tokens"], len(text), (time.perf_counter() - started) * 1000)
                return text.strip(), usage_data
            except Exception as exc:
                last_error = exc
                logger.warning("gemini.request_failed model=%s elapsed_ms=%.1f error_type=%s", model_name, (time.perf_counter() - started) * 1000, type(exc).__name__)

        logger.error("gemini.all_models_failed elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
        raise last_error or RuntimeError("Gemini returned no response.")
