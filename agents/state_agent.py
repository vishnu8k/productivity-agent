import os
import json
from google import genai
from google.genai import types
from typing import List
from models.schemas import Task, UserState, StateInputs
from engine.state_rules import detect_state
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite-preview-04-17")

COLD_START_QUESTION = (
    "Since we are just starting to work together, how are you feeling today, "
    "and how heavy has your workload been over the past few days?"
)

BACKFILL_PROMPT = """
Based on this user response about their recent workload and current state,
extract the following as JSON:
{{
  "current_state": "normal|energetic|fatigued|overwhelmed|constrained",
  "estimated_capacity": <integer 0-120 representing percentage of daily capacity used recently>
}}

User response: {response}

Return ONLY the JSON, no explanation.
"""


def run_state_agent(
    input_text: str,
    state_inputs: StateInputs,
    history: list,
    tasks: List[Task]
) -> UserState:
    return detect_state(input_text, state_inputs, history, tasks)


def needs_cold_start(history_days: int, input_text: str, state_inputs) -> bool:
    from engine.state_rules import detect_qualitative_state
    qualitative = detect_qualitative_state(input_text, state_inputs)
    if qualitative:
        return False
    return history_days < 3


def parse_cold_start_response(user_response: str) -> dict:
    prompt = BACKFILL_PROMPT.format(response=user_response)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json"
            )
        )
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"Cold start parse error: {e}")
        return {"current_state": "normal", "estimated_capacity": 50}
