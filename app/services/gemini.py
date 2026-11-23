from google import genai
from google.genai import types

from app.core.config import settings


client = genai.Client(
    api_key=settings.GEMINI_API_KEY
)


async def generate_response(
    prompt: str,
    system_instruction: str | None = None,
    thinking_level: str = "MEDIUM"
):

    response = await client.aio.models.generate_content(

        model=settings.GEMINI_MODEL,

        contents=prompt,

        config=types.GenerateContentConfig(

            system_instruction=system_instruction,

            thinking_config=types.ThinkingConfig(
                thinking_level=thinking_level
            )
        )
    )

    if not response.text:
        raise ValueError("Gemini returned an empty response")

    return {
        "text": response.text,

        "input_tokens": (
            response.usage_metadata.prompt_token_count
            if response.usage_metadata else 0
        ),

        "output_tokens": (
            response.usage_metadata.candidates_token_count
            if response.usage_metadata else 0
        ),

        "total_tokens": (
            response.usage_metadata.total_token_count
            if response.usage_metadata else 0
        )
    }