import asyncio

from app.services.gemini import generate_response


async def main():

    result = await generate_response(
        prompt="Explain what a REST API is in two sentences."
    )

    print("\nGemini Response:")
    print(result["text"])

    print("\nToken Usage:")
    print("Input:", result["input_tokens"])
    print("Output:", result["output_tokens"])
    print("Total:", result["total_tokens"])


if __name__ == "__main__":
    asyncio.run(main())